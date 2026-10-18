"""Explicit execution of local Bash scripts.

This is not a sandbox: the script runs with the current user's permissions.
The UI must show the plan and ask for confirmation before calling ``start``.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import signal
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from mkscript_studio.services.process import clean_environment


class ExecutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExecutionPlan:
    script: Path
    interpreter: str
    args: list[str] = field(default_factory=list)
    cwd: Path | None = None
    #: MKSCRIPT Studio never elevates privileges; shown to be explicit.
    elevated: bool = False

    @property
    def argv(self) -> list[str]:
        return [self.interpreter, str(self.script), *self.args]

    def command_line(self) -> str:
        return shlex.join(self.argv)


_RISK_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(^|[\s;&|(`])sudo\b"), "Uses sudo: without a terminal it cannot ask for the password, so that command will fail"),
    (re.compile(r"(^|[\s;&|(`])rm\b"), "Deletes files (rm)"),
    (re.compile(r"\bfind\b[^\n]*-delete\b"), "Deletes files (find -delete)"),
    (re.compile(r"(^|[\s;&|(`])(mv|cp)\b"), "Moves or copies files"),
    (re.compile(r"(^|[\s;&|(`])(dd|mkfs(\.\w+)?|shred|wipefs|fdisk|parted)\b"), "Writes directly to disks or destroys data irrecoverably"),
    (re.compile(r"(^|[\s;&|(`])(chmod|chown)\b"), "Changes permissions or owners"),
    (re.compile(r"(^|[\s;&|(`])(truncate)\b|[^<>&0-9]>(?!&|\s*/dev/null)\s*[\"'$\w/~.]"), "Writes or truncates files through redirection"),
    (re.compile(r"(^|[\s;&|(`])(kill|pkill|killall|systemctl\s+(stop|restart|disable|mask))\b"), "Stops processes or services"),
    (re.compile(r"(^|[\s;&|(`])(curl|wget)\b[^\n]*\|\s*(ba)?sh\b"), "Downloads and runs remote code"),
]


def analyze_risks(content: str) -> list[str]:
    """Look for command patterns that may modify the system (approximate analysis)."""
    code = "\n".join(line for line in content.splitlines() if not line.lstrip().startswith("#"))
    found: list[str] = []
    for pattern, message in _RISK_PATTERNS:
        if pattern.search(code) and message not in found:
            found.append(message)
    return found


def parse_arguments(text: str) -> list[str]:
    """Split arguments like the shell would, without expanding anything."""
    try:
        return shlex.split(text, comments=False, posix=True)
    except ValueError as exc:
        raise ExecutionError(f"Invalid arguments: {exc}") from None


def build_plan(script: Path, args_text: str = "", bash_path: str | None = None) -> ExecutionPlan:
    interpreter = bash_path or shutil.which("bash")
    if not interpreter:
        raise ExecutionError("Bash was not found on this system")
    if not script.is_file():
        raise ExecutionError(f"The file {script} does not exist; save it before running it")
    return ExecutionPlan(script=script, interpreter=interpreter, args=parse_arguments(args_text), cwd=script.parent)


OutputCallback = Callable[[str, str], None]
ExitCallback = Callable[[int | None, bool], None]


class ExecutionSession:
    """A running process. Callbacks are invoked from worker threads."""

    def __init__(self, plan: ExecutionPlan, on_output: OutputCallback, on_exit: ExitCallback) -> None:
        self.plan = plan
        self._on_output = on_output
        self._on_exit = on_exit
        self._proc: subprocess.Popen[bytes] | None = None
        self._cancelled = False
        self._readers: list[threading.Thread] = []

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> None:
        if self._proc is not None:
            raise ExecutionError("The execution has already started")
        try:
            self._proc = subprocess.Popen(
                self.plan.argv,  # argument list: never shell=True
                cwd=self.plan.cwd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=clean_environment(),
                start_new_session=os.name == "posix",
            )
        except OSError as exc:
            raise ExecutionError(f"Could not start the process: {exc.strerror or exc}") from None
        assert self._proc.stdout is not None and self._proc.stderr is not None
        for name, stream in (("stdout", self._proc.stdout), ("stderr", self._proc.stderr)):
            thread = threading.Thread(target=self._pump, args=(name, stream), daemon=True)
            thread.start()
            self._readers.append(thread)
        threading.Thread(target=self._wait, daemon=True).start()

    def _pump(self, name: str, stream) -> None:
        for chunk in iter(lambda: stream.read1(4096), b""):
            self._on_output(name, chunk.decode("utf-8", errors="replace"))
        stream.close()

    def _wait(self) -> None:
        assert self._proc is not None
        code = self._proc.wait()
        for thread in self._readers:
            thread.join()
        self._on_exit(code, self._cancelled)

    def cancel(self, grace_seconds: float = 3.0) -> None:
        """Send SIGTERM to the process group, then SIGKILL if it does not exit in time."""
        if not self.running:
            return
        self._cancelled = True
        assert self._proc is not None
        self._signal(signal.SIGTERM)

        def escalate() -> None:
            try:
                self._proc.wait(timeout=grace_seconds)
            except subprocess.TimeoutExpired:
                self._signal(getattr(signal, "SIGKILL", signal.SIGTERM))

        threading.Thread(target=escalate, daemon=True).start()

    def _signal(self, sig: int) -> None:
        assert self._proc is not None
        try:
            if os.name == "posix":
                os.killpg(self._proc.pid, sig)
            else:
                self._proc.send_signal(sig)
        except (ProcessLookupError, PermissionError):
            pass
