"""Safe reading and writing of the application's JSON documents.

Every store (preferences, state, projects, custom templates) uses these
functions: writes are atomic (temporary file + ``os.replace``) and a corrupt
file is moved aside under a timestamped name instead of being deleted.
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
    #: Where an unreadable file was moved to, if one was found.
    quarantined: Path | None = None
    error: str | None = None


def atomic_write_text(path: Path, text: str, mode: int | None = None) -> None:
    """Write ``text`` to ``path`` without ever leaving a half-written file."""
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
    """Return ``path`` + ``suffix`` (or with ``-N``) without clashing with anything existing."""
    candidate = path.with_name(path.name + suffix)
    counter = 1
    while candidate.exists() or candidate.is_symlink():
        candidate = path.with_name(f"{path.name}{suffix}-{counter}")
        counter += 1
    return candidate


def read_json(path: Path, default: Any) -> JsonLoadResult:
    """Read a JSON file. Returns ``default`` if missing; moves it aside if corrupt."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return JsonLoadResult(default)
    except OSError as exc:
        return JsonLoadResult(default, error=f"Could not read {path}: {exc.strerror or exc}")

    try:
        return JsonLoadResult(json.loads(raw))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = unique_sibling(path, f".corrupt-{stamp}")
        try:
            os.replace(path, target)
        except OSError:
            target = None
        return JsonLoadResult(default, quarantined=target, error=f"{path.name} was damaged: {exc}")
