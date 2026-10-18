"""Preferences persistence in ``$XDG_CONFIG_HOME/mkscript-studio``."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import fields, replace
from pathlib import Path
from typing import Any

from mkscript_studio import paths
from mkscript_studio.persistence import read_json, write_json
from mkscript_studio.preferences.models import Preferences, preferences_from_dict, validate_field

Listener = Callable[[Preferences, set[str]], None]


class PreferencesStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or paths.config_dir() / "preferences.json"
        self.warnings: list[str] = []
        self._listeners: list[Listener] = []
        self._extra: dict[str, Any] = {}
        self.preferences = self._load()

    def _load(self) -> Preferences:
        result = read_json(self.path, default={})
        if result.error:
            self.warnings.append(result.error)
        if result.quarantined:
            self.warnings.append(f"A copy of the damaged file was saved to {result.quarantined}")
        prefs, warnings = preferences_from_dict(result.data)
        self.warnings.extend(warnings)
        if isinstance(result.data, dict):
            # Unknown keys are kept (e.g. from newer versions).
            known = {f.name for f in fields(Preferences)} | {"version"}
            self._extra = {k: v for k, v in result.data.items() if k not in known}
        return prefs

    def subscribe(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def update(self, **changes: Any) -> Preferences:
        """Validate and save the changes; if any is invalid, nothing is saved."""
        validated = {name: validate_field(name, value) for name, value in changes.items()}
        changed = {n for n, v in validated.items() if getattr(self.preferences, n) != v}
        if not changed:
            return self.preferences
        new_prefs = replace(self.preferences, **validated)
        self._save(new_prefs)
        self.preferences = new_prefs
        for listener in list(self._listeners):
            listener(new_prefs, changed)
        return new_prefs

    def reset(self) -> Preferences:
        defaults = Preferences()
        return self.update(**{f.name: getattr(defaults, f.name) for f in fields(Preferences)})

    def _save(self, prefs: Preferences) -> None:
        write_json(self.path, {**self._extra, **prefs.to_dict()})
