"""Robust reading and saving of scripts."""

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
    """Error whose message is suitable for showing to the user."""


class InvalidNameError(ValueError):
    pass


class FileExistsConflict(FileServiceError):
    def __init__(self, path: Path) -> None:
        super().__init__(f"{path} already exists")
        self.path = path


@dataclass(frozen=True)
class SaveResult:
    path: Path
    created: bool
    backup_path: Path | None = None


def normalize_script_name(name: str) -> str:
    """Validate the name (same rules as mkscript) and add ``.sh`` if missing."""
    name = name.strip()
    if not name:
        raise InvalidNameError("Enter a name for the script")
    if name.startswith("-"):
        raise InvalidNameError("The name cannot start with '-'")
    if not _NAME_RE.match(name):
        raise InvalidNameError("Use only unaccented letters, digits, '.', '_' or '-'")
    if not name.endswith(".sh"):
        name += ".sh"
    if name.strip(".") in ("", "sh") or name.startswith("."):
        raise InvalidNameError("The name cannot start with '.' or be empty")
    if len(name.encode()) > 255:
        raise InvalidNameError("The name is too long")
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
    """Add x wherever r is already set (u, g, o); never removes permissions."""
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
            raise FileServiceError(f"{directory} exists but is not a folder") from None
        except PermissionError:
            raise FileServiceError(f"You do not have permission to create the folder {directory}") from None
        except OSError as exc:
            raise FileServiceError(f"Could not create the folder {directory}: {exc.strerror or exc}") from None
        if not directory.is_dir():
            raise FileServiceError(f"{directory} exists but is not a folder")
        return directory

    def read(self, path: Path) -> str:
        try:
            size = path.stat().st_size
            if size > MAX_FILE_SIZE:
                raise FileServiceError(f"{path.name} is {size // 1024} KiB; the limit is {MAX_FILE_SIZE // 1024 // 1024} MiB")
            data = path.read_bytes()
        except FileServiceError:
            raise
        except FileNotFoundError:
            raise FileServiceError(f"{path} does not exist") from None
        except IsADirectoryError:
            raise FileServiceError(f"{path} is a folder") from None
        except PermissionError:
            raise FileServiceError(f"You do not have permission to read {path}") from None
        except OSError as exc:
            raise FileServiceError(f"Could not read {path}: {exc.strerror or exc}") from None
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise FileServiceError(f"{path.name} is not UTF-8 encoded (byte {exc.start})") from None

    def save(
        self,
        path: Path,
        content: str,
        *,
        overwrite: bool = False,
        backup: BackupService | None = None,
        make_executable: bool = False,
    ) -> SaveResult:
        """Save ``content`` atomically.

        If the file exists and ``overwrite`` is false, ``FileExistsConflict`` is
        raised so the UI can ask for confirmation. With ``backup`` a copy is made
        before replacing it; if the copy fails, the original file is left untouched.
        """
        if path.is_symlink():
            # Update the link target, not the link itself.
            path = Path(os.path.realpath(path))
        if path.is_dir():
            raise FileServiceError(f"{path} is a folder")

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
            raise FileServiceError(f"You do not have permission to write to {path.parent}") from None
        except OSError as exc:
            raise FileServiceError(f"Could not write to {path.parent}: {exc.strerror or exc}") from None
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
                raise FileServiceError(f"You do not have permission to save {path}") from None
            raise FileServiceError(f"Could not save {path}: {exc.strerror or exc}") from None
