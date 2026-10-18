"""Timestamped backups before overwriting a file."""

from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path


class BackupError(OSError):
    pass


class BackupService:
    """Copy a file to ``<name>.<YYYYMMDD-HHMMSS>.bak`` without clobbering existing backups.

    Old backups are never removed automatically.
    """

    def __init__(self, backup_dir: Path | None = None, clock=datetime.now) -> None:
        #: ``None`` = store the backup next to the original file.
        self.backup_dir = backup_dir
        self._clock = clock

    def backup_name(self, source: Path) -> str:
        return f"{source.name}.{self._clock().strftime('%Y%m%d-%H%M%S')}.bak"

    def target_for(self, source: Path) -> Path:
        directory = self.backup_dir or source.parent
        name = self.backup_name(source)
        candidate = directory / name
        counter = 1
        while candidate.exists() or candidate.is_symlink():
            stem = name.removesuffix(".bak")
            candidate = directory / f"{stem}-{counter}.bak"
            counter += 1
        return candidate

    def create_backup(self, source: Path) -> Path:
        """Create the backup and return its path; raise ``BackupError`` if not possible."""
        if not source.is_file():
            raise BackupError(f"Cannot back up {source}: it is not a file")
        try:
            directory = self.backup_dir or source.parent
            directory.mkdir(parents=True, exist_ok=True)
            target = self.target_for(source)
            # "x" guarantees we never clobber a file that appeared in the meantime.
            with open(source, "rb") as src, open(target, "xb") as dst:
                shutil.copyfileobj(src, dst)
                dst.flush()
                os.fsync(dst.fileno())
            shutil.copystat(source, target)
        except FileExistsError as exc:
            raise BackupError(f"The backup {exc.filename} appeared while it was being created; try again") from exc
        except OSError as exc:
            raise BackupError(f"Could not create a backup of {source}: {exc.strerror or exc}") from exc
        return target
