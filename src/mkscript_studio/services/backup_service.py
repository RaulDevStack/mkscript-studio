"""Copias de seguridad fechadas antes de sobrescribir un archivo."""

from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path


class BackupError(OSError):
    pass


class BackupService:
    """Copia un archivo a ``<nombre>.<AAAAMMDD-HHMMSS>.bak`` sin pisar copias existentes.

    Las copias antiguas no se eliminan nunca de forma automática.
    """

    def __init__(self, backup_dir: Path | None = None, clock=datetime.now) -> None:
        #: ``None`` = guardar la copia junto al archivo original.
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
        """Crea la copia y devuelve su ruta; lanza ``BackupError`` si no es posible."""
        if not source.is_file():
            raise BackupError(f"No se puede copiar {source}: no es un archivo")
        try:
            directory = self.backup_dir or source.parent
            directory.mkdir(parents=True, exist_ok=True)
            target = self.target_for(source)
            # «x» garantiza que nunca se pisa un archivo que apareciera entre medias.
            with open(source, "rb") as src, open(target, "xb") as dst:
                shutil.copyfileobj(src, dst)
                dst.flush()
                os.fsync(dst.fileno())
            shutil.copystat(source, target)
        except FileExistsError as exc:
            raise BackupError(f"La copia {exc.filename} apareció mientras se creaba; inténtalo de nuevo") from exc
        except OSError as exc:
            raise BackupError(f"No se pudo crear la copia de seguridad de {source}: {exc.strerror or exc}") from exc
        return target
