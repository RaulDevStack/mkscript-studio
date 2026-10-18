"""Lectura y guardado robusto de scripts."""

from __future__ import annotations

import os
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from mkscript_studio.paths import expand_user_path
from mkscript_studio.services.backup_service import BackupError, BackupService

MAX_FILE_SIZE = 5 * 1024 * 1024
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class FileServiceError(OSError):
    """Error con un mensaje apto para mostrarse al usuario."""


class InvalidNameError(ValueError):
    pass


class FileExistsConflict(FileServiceError):
    def __init__(self, path: Path) -> None:
        super().__init__(f"Ya existe {path}")
        self.path = path


@dataclass(frozen=True)
class SaveResult:
    path: Path
    created: bool
    backup_path: Path | None = None


def normalize_script_name(name: str) -> str:
    """Valida el nombre (mismas reglas que mkscript) y añade ``.sh`` si falta."""
    name = name.strip()
    if not name:
        raise InvalidNameError("Escribe un nombre para el script")
    if name.startswith("-"):
        raise InvalidNameError("El nombre no puede empezar por «-»")
    if not _NAME_RE.match(name):
        raise InvalidNameError("Usa solo letras sin acentos, números, «.», «_» o «-»")
    if not name.endswith(".sh"):
        name += ".sh"
    if name.strip(".") in ("", "sh") or name.startswith("."):
        raise InvalidNameError("El nombre no puede empezar por «.» ni estar vacío")
    if len(name.encode()) > 255:
        raise InvalidNameError("El nombre es demasiado largo")
    return name


def _current_umask() -> int:
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("Umask:"):
                return int(line.split()[1], 8)
    except (OSError, ValueError, IndexError):
        pass
    return 0o022


def _with_exec_bits(mode: int) -> int:
    """Añade x donde ya hay r (u, g, o); nunca quita permisos."""
    if mode & stat.S_IRUSR:
        mode |= stat.S_IXUSR
    if mode & stat.S_IRGRP:
        mode |= stat.S_IXGRP
    if mode & stat.S_IROTH:
        mode |= stat.S_IXOTH
    return mode


class FileService:
    def resolve_output_path(self, directory: str, name: str) -> Path:
        return expand_user_path(directory) / normalize_script_name(name)

    def ensure_directory(self, directory: Path) -> Path:
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except FileExistsError:
            raise FileServiceError(f"{directory} existe pero no es una carpeta") from None
        except PermissionError:
            raise FileServiceError(f"No tienes permiso para crear la carpeta {directory}") from None
        except OSError as exc:
            raise FileServiceError(f"No se pudo crear la carpeta {directory}: {exc.strerror or exc}") from None
        if not directory.is_dir():
            raise FileServiceError(f"{directory} existe pero no es una carpeta")
        return directory

    def read(self, path: Path) -> str:
        try:
            size = path.stat().st_size
            if size > MAX_FILE_SIZE:
                raise FileServiceError(f"{path.name} ocupa {size // 1024} KiB; el límite es {MAX_FILE_SIZE // 1024 // 1024} MiB")
            data = path.read_bytes()
        except FileServiceError:
            raise
        except FileNotFoundError:
            raise FileServiceError(f"No existe {path}") from None
        except IsADirectoryError:
            raise FileServiceError(f"{path} es una carpeta") from None
        except PermissionError:
            raise FileServiceError(f"No tienes permiso para leer {path}") from None
        except OSError as exc:
            raise FileServiceError(f"No se pudo leer {path}: {exc.strerror or exc}") from None
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise FileServiceError(f"{path.name} no está codificado en UTF-8 (byte {exc.start})") from None

    def save(
        self,
        path: Path,
        content: str,
        *,
        overwrite: bool = False,
        backup: BackupService | None = None,
        make_executable: bool = False,
    ) -> SaveResult:
        """Guarda ``content`` de forma atómica.

        Si el archivo existe y ``overwrite`` es falso se lanza ``FileExistsConflict``
        para que la interfaz pida confirmación. Con ``backup`` se crea una copia
        antes de reemplazarlo; si la copia falla, el archivo original no se toca.
        """
        if path.is_symlink():
            # Se actualiza el destino del enlace, no el enlace en sí.
            path = Path(os.path.realpath(path))
        if path.is_dir():
            raise FileServiceError(f"{path} es una carpeta")

        exists = path.exists()
        if exists and not overwrite:
            raise FileExistsConflict(path)

        self.ensure_directory(path.parent)

        backup_path = None
        if exists:
            mode = stat.S_IMODE(path.stat().st_mode)
            if backup is not None:
                try:
                    backup_path = backup.create_backup(path)
                except BackupError as exc:
                    raise FileServiceError(str(exc)) from exc
        else:
            mode = 0o666 & ~_current_umask()
        if make_executable:
            mode = _with_exec_bits(mode)

        self._atomic_write(path, content.encode("utf-8"), mode)
        return SaveResult(path=path, created=not exists, backup_path=backup_path)

    @staticmethod
    def _atomic_write(path: Path, data: bytes, mode: int) -> None:
        try:
            fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        except PermissionError:
            raise FileServiceError(f"No tienes permiso para escribir en {path.parent}") from None
        except OSError as exc:
            raise FileServiceError(f"No se pudo escribir en {path.parent}: {exc.strerror or exc}") from None
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp_name, mode)
            os.replace(tmp_name, path)
        except OSError as exc:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass
            if isinstance(exc, PermissionError):
                raise FileServiceError(f"No tienes permiso para guardar {path}") from None
            raise FileServiceError(f"No se pudo guardar {path}: {exc.strerror or exc}") from None
