"""User directories (XDG) and path expansion helpers."""

from __future__ import annotations

import os
from pathlib import Path

APP_DIR_NAME = "mkscript-studio"


def expand_user_path(value: str | os.PathLike[str]) -> Path:
    """Expand ``~`` and environment variables (``$HOME``) and normalize the path.

    Does not resolve symlinks or require the path to exist.
    """
    text = os.fspath(value).strip()
    if not text:
        raise ValueError("The path is empty")
    expanded = os.path.expanduser(os.path.expandvars(text))
    return Path(os.path.normpath(expanded))


def _xdg_dir(env_name: str, fallback: str) -> Path:
    raw = os.environ.get(env_name, "").strip()
    # The XDG spec requires absolute paths; relative ones are ignored.
    if raw and os.path.isabs(raw):
        return Path(raw)
    return Path.home() / fallback


def config_dir() -> Path:
    return _xdg_dir("XDG_CONFIG_HOME", ".config") / APP_DIR_NAME


def data_dir() -> Path:
    return _xdg_dir("XDG_DATA_HOME", ".local/share") / APP_DIR_NAME


def resources_dir() -> Path:
    return Path(__file__).resolve().parent / "resources"
