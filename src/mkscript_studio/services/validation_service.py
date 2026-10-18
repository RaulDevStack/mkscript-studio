"""Syntax check (``bash -n``) and optional analysis with ShellCheck.

None of these checks runs the script. Correct syntax does not guarantee that
the script is safe or does what you expect.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from dataclasses import dataclass, field
from enum import Enum

from mkscript_studio.services.process import CancellableProcess


class CheckStatus(Enum):
    OK = "ok"
    ISSUES = "issues"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True)
class Diagnostic:
    severity: str  # error | warning | info | style
    message: str
    source: str  # bash | shellcheck
    line: int | None = None
    column: int | None = None
    code: str | None = None
    detail: str | None = None


@dataclass
class CheckResult:
    tool: str
    status: CheckStatus
    diagnostics: list[Diagnostic] = field(default_factory=list)
    summary: str = ""
    duration: float = 0.0


_BASH_LINE_RE = re.compile(r"^(?:.*?: )?line (\d+): (.*)$")


def parse_bash_errors(stderr: str) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for raw in stderr.splitlines():
        text = raw.strip()
        if not text:
            continue
        match = _BASH_LINE_RE.match(text)
        if not match:
            diagnostics.append(Diagnostic("error", text, "bash"))
            continue
        line, message = int(match.group(1)), match.group(2).strip()
        # Bash repeats the offending line ("`fi'") in a second message.
        if (message.startswith("`") and message.endswith("'")) and diagnostics and diagnostics[-1].line == line:
            previous = diagnostics.pop()
            diagnostics.append(Diagnostic(previous.severity, previous.message, "bash", line, detail=message[1:-1]))
            continue
        diagnostics.append(Diagnostic("error", message, "bash", line))
    return diagnostics


def parse_shellcheck_json(stdout: str) -> list[Diagnostic]:
    data = json.loads(stdout or "{}")
    comments = data.get("comments", []) if isinstance(data, dict) else data
    result = []
    for item in comments:
        result.append(Diagnostic(
            severity=str(item.get("level", "warning")),
            message=str(item.get("message", "")),
            source="shellcheck",
            line=item.get("line"),
            column=item.get("column"),
            code=f"SC{item['code']}" if "code" in item else None,
        ))
    result.sort(key=lambda d: (d.line or 0, d.column or 0))
    return result


class ValidationService:
    def __init__(self, bash_path: str | None = None, shellcheck_path: str | None = None, timeout: float = 10.0) -> None:
        self.bash_path = bash_path if bash_path is not None else shutil.which("bash")
        self.shellcheck_path = shellcheck_path if shellcheck_path is not None else shutil.which("shellcheck")
        self.timeout = timeout

    @property
    def bash_available(self) -> bool:
        return bool(self.bash_path) and shutil.which(self.bash_path) is not None

    @property
    def shellcheck_available(self) -> bool:
        return bool(self.shellcheck_path) and shutil.which(self.shellcheck_path) is not None

    def check_syntax(self, content: str, job: CancellableProcess | None = None) -> CheckResult:
        if not self.bash_available:
            return CheckResult("bash -n", CheckStatus.UNAVAILABLE,
                               summary="Bash was not found; install the bash package to check syntax.")
        job = job or CancellableProcess()
        start = time.monotonic()
        # -n: read and parse without executing. The code arrives on stdin.
        output = job.run([self.bash_path, "--noprofile", "--norc", "-n"], content, self.timeout)
        duration = time.monotonic() - start
        if output.cancelled:
            return CheckResult("bash -n", CheckStatus.CANCELLED, summary="Check cancelled", duration=duration)
        if output.timed_out:
            return CheckResult("bash -n", CheckStatus.TIMEOUT, duration=duration,
                               summary=f"The check exceeded the {self.timeout:g} s limit")
        if output.returncode == 0 and not output.stderr.strip():
            return CheckResult("bash -n", CheckStatus.OK, duration=duration,
                               summary="Syntax OK. This does not guarantee that the script is safe.")
        diagnostics = parse_bash_errors(output.stderr)
        if not diagnostics:
            diagnostics = [Diagnostic("error", f"bash exited with code {output.returncode}", "bash")]
        count = len(diagnostics)
        return CheckResult("bash -n", CheckStatus.ISSUES, diagnostics, duration=duration,
                           summary=f"{count} syntax error{'s' if count != 1 else ''}")

    def run_shellcheck(self, content: str, job: CancellableProcess | None = None) -> CheckResult:
        if not self.shellcheck_available:
            return CheckResult("ShellCheck", CheckStatus.UNAVAILABLE,
                               summary="ShellCheck is not installed (it is optional).")
        job = job or CancellableProcess()
        start = time.monotonic()
        output = job.run([self.shellcheck_path, "--format=json1", "--shell=bash", "-"], content, self.timeout * 3)
        duration = time.monotonic() - start
        if output.cancelled:
            return CheckResult("ShellCheck", CheckStatus.CANCELLED, summary="Analysis cancelled", duration=duration)
        if output.timed_out:
            return CheckResult("ShellCheck", CheckStatus.TIMEOUT, duration=duration,
                               summary="ShellCheck exceeded the time limit")
        if output.returncode not in (0, 1):
            return CheckResult("ShellCheck", CheckStatus.FAILED, duration=duration,
                               summary=output.stderr.strip() or f"ShellCheck exited with code {output.returncode}")
        try:
            diagnostics = parse_shellcheck_json(output.stdout)
        except (json.JSONDecodeError, TypeError, AttributeError) as exc:
            return CheckResult("ShellCheck", CheckStatus.FAILED, duration=duration,
                               summary=f"Could not parse the ShellCheck output: {exc}")
        if not diagnostics:
            return CheckResult("ShellCheck", CheckStatus.OK, duration=duration, summary="ShellCheck found no issues")
        return CheckResult("ShellCheck", CheckStatus.ISSUES, diagnostics, duration=duration,
                           summary=f"{len(diagnostics)} ShellCheck warning(s)")
