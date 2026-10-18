"""Lectura y escritura segura de documentos JSON de la aplicación.

Todos los almacenes (preferencias, estado, proyectos, plantillas propias)
usan estas funciones: la escritura es atómica (archivo temporal + ``os.replace``)
y un archivo corrupto se aparta con un nombre fechado en lugar de borrarse.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class JsonLoadResult:
    data: Any
    #: Ruta a la que se movió un archivo ilegible, si se encontró uno.
    quarantined: Path | None = None
    error: str | None = None


def atomic_write_text(path: Path, text: str, mode: int | None = None) -> None:
    """Escribe ``text`` en ``path`` sin dejar nunca un archivo a medias."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def write_json(path: Path, data: Any) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n")


def unique_sibling(path: Path, suffix: str) -> Path:
    """Devuelve ``path`` + ``suffix`` (o con ``-N``) sin colisionar con nada existente."""
    candidate = path.with_name(path.name + suffix)
    counter = 1
    while candidate.exists() or candidate.is_symlink():
        candidate = path.with_name(f"{path.name}{suffix}-{counter}")
        counter += 1
    return candidate


def read_json(path: Path, default: Any) -> JsonLoadResult:
    """Lee un JSON. Si no existe devuelve ``default``; si está corrupto lo aparta."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return JsonLoadResult(default)
    except OSError as exc:
        return JsonLoadResult(default, error=f"No se pudo leer {path}: {exc.strerror or exc}")

    try:
        return JsonLoadResult(json.loads(raw))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = unique_sibling(path, f".corrupt-{stamp}")
        try:
            os.replace(path, target)
        except OSError:
            target = None
        return JsonLoadResult(default, quarantined=target, error=f"{path.name} estaba dañado: {exc}")
