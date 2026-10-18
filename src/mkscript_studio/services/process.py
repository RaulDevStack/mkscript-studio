"""Running helper processes with a time limit and cancellation."""

from __future__ import annotations

import os
import subprocess
import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class ProcessOutput:
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    cancelled: bool = False


def clean_environment() -> dict[str, str]:
    """Environment without variables that would make Bash run files at startup."""
    env = dict(os.environ)
    for name in ("BASH_ENV", "ENV", "SHELLOPTS", "BASHOPTS", "PROMPT_COMMAND"):
        env.pop(name, None)
    env["LC_ALL"] = env.get("LC_ALL") or "C.UTF-8"
    return env


class CancellableProcess:
    """Run a single process; ``cancel()`` may be called from another thread."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[bytes] | None = None
        self._cancelled = False

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            if self._proc is not None and self._proc.poll() is None:
                self._proc.kill()

    def run(self, argv: list[str], input_text: str | None = None, timeout: float = 10.0) -> ProcessOutput:
        with self._lock:
            if self._cancelled:
                return ProcessOutput(None, "", "", cancelled=True)
            self._proc = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=clean_environment(),
            )
        data = input_text.encode("utf-8") if input_text is not None else None
        try:
            out, err = self._proc.communicate(data, timeout=timeout)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            out, err = self._proc.communicate()
            return ProcessOutput(None, _decode(out), _decode(err), timed_out=True)
        return ProcessOutput(
            None if self._cancelled else self._proc.returncode,
            _decode(out),
            _decode(err),
            cancelled=self._cancelled,
        )


def _decode(data: bytes | None) -> str:
    return (data or b"").decode("utf-8", errors="replace")
