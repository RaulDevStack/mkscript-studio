"""Servicios compartidos por la interfaz (sin dependencias de GTK)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from mkscript_studio import paths
from mkscript_studio.generator.templates import TemplateRegistry
from mkscript_studio.preferences.store import PreferencesStore
from mkscript_studio.services.backup_service import BackupService
from mkscript_studio.services.file_service import FileService
from mkscript_studio.services.project_service import ProjectService
from mkscript_studio.services.state_service import StateService
from mkscript_studio.services.validation_service import ValidationService


@dataclass
class AppServices:
    prefs: PreferencesStore
    state: StateService
    projects: ProjectService
    templates: TemplateRegistry
    files: FileService = field(default_factory=FileService)
    validation: ValidationService = field(default_factory=ValidationService)

    @classmethod
    def create(cls, config_dir: Path | None = None, data_dir: Path | None = None) -> AppServices:
        config_dir = config_dir or paths.config_dir()
        data_dir = data_dir or paths.data_dir()
        return cls(
            prefs=PreferencesStore(config_dir / "preferences.json"),
            state=StateService(data_dir / "state.json"),
            projects=ProjectService(data_dir / "projects.json"),
            templates=TemplateRegistry(data_dir / "templates"),
        )

    def backup_service(self) -> BackupService | None:
        prefs = self.prefs.preferences
        return BackupService(prefs.backup_path()) if prefs.backup_before_overwrite else None

    def startup_warnings(self) -> list[str]:
        return [*self.prefs.warnings, *self.state.warnings, *self.projects.warnings, *self.templates.warnings]
