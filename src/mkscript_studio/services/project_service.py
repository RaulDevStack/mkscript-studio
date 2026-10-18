"""Projects: folders of related scripts with a small metadata file.

Removing a project from the list never deletes its files.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from mkscript_studio import paths
from mkscript_studio.persistence import read_json, write_json

METADATA_FILE = ".mkscript-project.json"


class ProjectError(OSError):
    pass


@dataclass(frozen=True)
class Project:
    path: Path
    name: str
    description: str = ""
    created: str = ""

    @property
    def exists(self) -> bool:
        return self.path.is_dir()


def _folder_name(name: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in "-_." else "-" for c in name.strip())
    cleaned = cleaned.strip("-.")
    if not cleaned:
        raise ProjectError("The project name is not valid")
    return cleaned


class ProjectService:
    def __init__(self, index_path: Path | None = None) -> None:
        self.index_path = index_path or paths.data_dir() / "projects.json"
        result = read_json(self.index_path, default=[])
        self.warnings = [w for w in (result.error,) if w]
        data = result.data if isinstance(result.data, list) else []
        self._paths: list[str] = [p for p in data if isinstance(p, str)]

    def _save_index(self) -> None:
        write_json(self.index_path, self._paths)

    @staticmethod
    def _read_metadata(directory: Path) -> Project:
        result = read_json(directory / METADATA_FILE, default={})
        data = result.data if isinstance(result.data, dict) else {}
        return Project(
            path=directory,
            name=str(data.get("name") or directory.name),
            description=str(data.get("description", "")),
            created=str(data.get("created", "")),
        )

    @staticmethod
    def _write_metadata(project: Project) -> None:
        try:
            write_json(project.path / METADATA_FILE, {
                "name": project.name, "description": project.description, "created": project.created,
            })
        except OSError as exc:
            raise ProjectError(f"Could not save the project data: {exc.strerror or exc}") from None

    def list_projects(self) -> list[Project]:
        projects = []
        for raw in self._paths:
            directory = Path(raw)
            projects.append(self._read_metadata(directory) if directory.is_dir() else Project(directory, directory.name))
        return projects

    def _register(self, directory: Path) -> None:
        text = str(directory)
        self._paths = [text, *[p for p in self._paths if p != text]]
        self._save_index()

    def create(self, name: str, parent: Path, description: str = "") -> Project:
        directory = parent / _folder_name(name)
        if directory.exists():
            raise ProjectError(f"{directory} already exists; choose another name or add it as a project")
        try:
            directory.mkdir(parents=True)
        except OSError as exc:
            raise ProjectError(f"Could not create {directory}: {exc.strerror or exc}") from None
        project = Project(directory, " ".join(name.split()), " ".join(description.split()),
                          datetime.now().isoformat(timespec="seconds"))
        self._write_metadata(project)
        self._register(directory)
        return project

    def add_existing(self, directory: Path) -> Project:
        if not directory.is_dir():
            raise ProjectError(f"{directory} is not a folder")
        if not (directory / METADATA_FILE).exists():
            self._write_metadata(Project(directory, directory.name, "", datetime.now().isoformat(timespec="seconds")))
        self._register(directory)
        return self._read_metadata(directory)

    def rename(self, project: Project, new_name: str) -> Project:
        new_name = " ".join(new_name.split())
        if not new_name:
            raise ProjectError("The name cannot be empty")
        renamed = Project(project.path, new_name, project.description, project.created)
        self._write_metadata(renamed)
        return renamed

    def duplicate(self, project: Project, new_name: str) -> Project:
        if not project.exists:
            raise ProjectError(f"The folder {project.path} does not exist")
        target = project.path.parent / _folder_name(new_name)
        if target.exists():
            raise ProjectError(f"{target} already exists; choose another name")
        try:
            shutil.copytree(project.path, target, symlinks=True)
        except OSError as exc:
            raise ProjectError(f"Could not duplicate the project: {exc}") from None
        copy = Project(target, " ".join(new_name.split()), project.description,
                       datetime.now().isoformat(timespec="seconds"))
        self._write_metadata(copy)
        self._register(target)
        return copy

    def remove_from_list(self, project: Project) -> None:
        self._paths = [p for p in self._paths if p != str(project.path)]
        self._save_index()

    @staticmethod
    def files(project: Project) -> list[Path]:
        if not project.exists:
            return []
        entries = [p for p in project.path.iterdir() if p.is_file() and not p.name.startswith(".")]
        return sorted(entries, key=lambda p: (p.suffix != ".sh", p.name.lower()))
