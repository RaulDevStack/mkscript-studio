"""Helpers to safely insert user values into Bash code."""

from __future__ import annotations

import posixpath
import re

_HOME_PREFIX_RE = re.compile(r"^(~|\$HOME|\$\{HOME\})(?=/|$)")


def single_quote(value: str) -> str:
    """Single-quoted Bash literal; nothing inside it is interpreted."""
    return "'" + value.replace("'", "'\\''") + "'"


def _escape_double_quoted(value: str) -> str:
    return re.sub(r'([\\"$`])', r"\\\1", value)


def split_home(path: str) -> tuple[bool, str]:
    """Split the ``~``/``$HOME`` prefix from the rest of the path."""
    match = _HOME_PREFIX_RE.match(path)
    if match:
        return True, path[match.end():]
    return False, path


def path_literal(path: str) -> str:
    """Turn a path from the form into a Bash literal.

    ``~/x`` and ``$HOME/x`` become ``"${HOME}/x"`` so they expand on the machine
    that runs the script; any other special character is escaped.
    """
    under_home, rest = split_home(path.strip())
    if under_home:
        return '"${HOME}' + _escape_double_quoted(rest) + '"'
    return single_quote(path.strip())


def path_key(path: str) -> str:
    """Normalized form of a path from the form, useful for comparing paths."""
    under_home, rest = split_home(path.strip())
    normalized = posixpath.normpath(("~" if under_home else "") + rest) if (rest or under_home) else ""
    return normalized


def is_absolute_or_home(path: str) -> bool:
    under_home, _ = split_home(path.strip())
    return under_home or path.strip().startswith("/")


def array_literal(values: list[str]) -> str:
    return "(" + " ".join(single_quote(v) for v in values) + ")"
