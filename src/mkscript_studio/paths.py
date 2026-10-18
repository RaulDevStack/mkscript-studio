"""Rutas de usuario (XDG) y utilidades de expansión de rutas."""

from __future__ import annotations

import os
from pathlib import Path

APP_DIR_NAME = "mkscript-studio"


def expand_user_path(value: str | os.PathLike[str]) -> Path:
    """Expande ``~`` y variables de entorno (``$HOME``) y normaliza la ruta.

    No resuelve enlaces simbólicos ni exige que la ruta exista.
    """
    text = os.fspath(value).strip()
    if not text:
        raise ValueError("La ruta está vacía")
    expanded = os.path.expanduser(os.path.expandvars(text))
    return Path(os.path.normpath(expanded))


def _xdg_dir(env_name: str, fallback: str) -> Path:
    raw = os.environ.get(env_name, "").strip()
    # La especificación XDG exige rutas absolutas; las relativas se ignoran.
    if raw and os.path.isabs(raw):
        return Path(raw)
    return Path.home() / fallback


def config_dir() -> Path:
    return _xdg_dir("XDG_CONFIG_HOME", ".config") / APP_DIR_NAME


def data_dir() -> Path:
    return _xdg_dir("XDG_DATA_HOME", ".local/share") / APP_DIR_NAME


def resources_dir() -> Path:
    return Path(__file__).resolve().parent / "resources"
