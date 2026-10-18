"""Utilidades para insertar valores del usuario en código Bash de forma segura."""

from __future__ import annotations

import posixpath
import re

_HOME_PREFIX_RE = re.compile(r"^(~|\$HOME|\$\{HOME\})(?=/|$)")


def single_quote(value: str) -> str:
    """Literal Bash entre comillas simples; nada en su interior se interpreta."""
    return "'" + value.replace("'", "'\\''") + "'"


def _escape_double_quoted(value: str) -> str:
    return re.sub(r'([\\"$`])', r"\\\1", value)


def split_home(path: str) -> tuple[bool, str]:
    """Separa el prefijo ``~``/``$HOME`` del resto de la ruta."""
    match = _HOME_PREFIX_RE.match(path)
    if match:
        return True, path[match.end():]
    return False, path


def path_literal(path: str) -> str:
    """Convierte una ruta del formulario en un literal Bash.

    ``~/x`` y ``$HOME/x`` se convierten en ``"${HOME}/x"`` para que se expandan
    en la máquina que ejecuta el script; cualquier otro carácter especial se escapa.
    """
    under_home, rest = split_home(path.strip())
    if under_home:
        return '"${HOME}' + _escape_double_quoted(rest) + '"'
    return single_quote(path.strip())


def path_key(path: str) -> str:
    """Forma normalizada de una ruta del formulario, útil para compararlas."""
    under_home, rest = split_home(path.strip())
    normalized = posixpath.normpath(("~" if under_home else "") + rest) if (rest or under_home) else ""
    return normalized


def is_absolute_or_home(path: str) -> bool:
    under_home, _ = split_home(path.strip())
    return under_home or path.strip().startswith("/")


def array_literal(values: list[str]) -> str:
    return "(" + " ".join(single_quote(v) for v in values) + ")"
