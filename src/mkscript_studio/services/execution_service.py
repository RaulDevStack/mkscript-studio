"""Ejecución explícita de scripts Bash locales.

No es un sandbox: el script se ejecuta con los permisos del usuario actual.
La interfaz debe mostrar el plan y pedir confirmación antes de llamar a ``start``.
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
    #: MKSCRIPT Studio nunca eleva privilegios; se muestra para ser explícitos.
    elevated: bool = False

    @property
    def argv(self) -> list[str]:
        return [self.interpreter, str(self.script), *self.args]

    def command_line(self) -> str:
        return shlex.join(self.argv)


_RISK_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(^|[\s;&|(`])sudo\b"), "Usa sudo: sin terminal no podrá pedir la contraseña y esa orden fallará"),
    (re.compile(r"(^|[\s;&|(`])rm\b"), "Borra archivos (rm)"),
    (re.compile(r"\bfind\b[^\n]*-delete\b"), "Borra archivos (find -delete)"),
    (re.compile(r"(^|[\s;&|(`])(mv|cp)\b"), "Mueve o copia archivos"),
    (re.compile(r"(^|[\s;&|(`])(dd|mkfs(\.\w+)?|shred|wipefs|fdisk|parted)\b"), "Escribe directamente en discos o borra datos de forma irrecuperable"),
    (re.compile(r"(^|[\s;&|(`])(chmod|chown)\b"), "Cambia permisos o propietarios"),
    (re.compile(r"(^|[\s;&|(`])(truncate)\b|[^<>&0-9]>(?!&|\s*/dev/null)\s*[\"'$\w/~.]"), "Escribe o vacía archivos mediante redirección"),
    (re.compile(r"(^|[\s;&|(`])(kill|pkill|killall|systemctl\s+(stop|restart|disable|mask))\b"), "Detiene procesos o servicios"),
    (re.compile(r"(^|[\s;&|(`])(curl|wget)\b[^\n]*\|\s*(ba)?sh\b"), "Descarga y ejecuta código remoto"),
]


def analyze_risks(content: str) -> list[str]:
    """Busca patrones de órdenes que pueden modificar el sistema (análisis aproximado)."""
    code = "\n".join(line for line in content.splitlines() if not line.lstrip().startswith("#"))
    found: list[str] = []
    for pattern, message in _RISK_PATTERNS:
        if pattern.search(code) and message not in found:
            found.append(message)
    return found


def parse_arguments(text: str) -> list[str]:
    """Separa los argumentos como lo haría el shell, sin expandir nada."""
    try:
        return shlex.split(text, comments=False, posix=True)
    except ValueError as exc:
        raise ExecutionError(f"Argumentos no válidos: {exc}") from None


def build_plan(script: Path, args_text: str = "", bash_path: str | None = None) -> ExecutionPlan:
    interpreter = bash_path or shutil.which("bash")
    if not interpreter:
        raise ExecutionError("No se encontró Bash en el sistema")
    if not script.is_file():
        raise ExecutionError(f"No existe el archivo {script}; guárdalo antes de ejecutarlo")
    return ExecutionPlan(script=script, interpreter=interpreter, args=parse_arguments(args_text), cwd=script.parent)


OutputCallback = Callable[[str, str], None]
ExitCallback = Callable[[int | None, bool], None]


class ExecutionSession:
    """Proceso en ejecución. Los callbacks se invocan desde hilos secundarios."""

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
            raise ExecutionError("La ejecución ya se inició")
        try:
            self._proc = subprocess.Popen(
                self.plan.argv,  # lista de argumentos: nunca shell=True
                cwd=self.plan.cwd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=clean_environment(),
                start_new_session=os.name == "posix",
            )
        except OSError as exc:
            raise ExecutionError(f"No se pudo iniciar el proceso: {exc.strerror or exc}") from None
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
        """Envía SIGTERM al grupo de procesos y SIGKILL si no termina a tiempo."""
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
