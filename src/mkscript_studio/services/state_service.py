"""Application state: recent files and favorite templates."""

from __future__ import annotations

from pathlib import Path

from mkscript_studio import paths
from mkscript_studio.persistence import read_json, write_json

MAX_RECENT = 15


class StateService:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or paths.data_dir() / "state.json"
        result = read_json(self.path, default={})
        self.warnings = [w for w in (result.error,) if w]
        data = result.data if isinstance(result.data, dict) else {}
        self.recent_files: list[str] = [p for p in data.get("recent_files", []) if isinstance(p, str)][:MAX_RECENT]
        self.favorite_templates: list[str] = [t for t in data.get("favorite_templates", []) if isinstance(t, str)]

    def _save(self) -> None:
        write_json(self.path, {"recent_files": self.recent_files, "favorite_templates": self.favorite_templates})

    def add_recent(self, path: Path) -> None:
        text = str(path)
        self.recent_files = [text, *[p for p in self.recent_files if p != text]][:MAX_RECENT]
        self._save()

    def remove_recent(self, path: Path) -> None:
        self.recent_files = [p for p in self.recent_files if p != str(path)]
        self._save()

    def existing_recent_files(self) -> list[Path]:
        return [Path(p) for p in self.recent_files if Path(p).is_file()]

    def is_favorite(self, template_id: str) -> bool:
        return template_id in self.favorite_templates

    def set_favorite(self, template_id: str, favorite: bool) -> None:
        if favorite and template_id not in self.favorite_templates:
            self.favorite_templates.append(template_id)
        elif not favorite and template_id in self.favorite_templates:
            self.favorite_templates.remove(template_id)
        else:
            return
        self._save()
