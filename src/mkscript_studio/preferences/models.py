"""Preferences model and validation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from mkscript_studio.paths import expand_user_path

SCHEMA_VERSION = 1

#: Shells the generator can produce code for. Zsh and POSIX sh will be listed
#: here once the engine can generate correct code for them.
SUPPORTED_SHELLS: dict[str, str] = {"bash": "Bash"}

COMMENT_STYLES: dict[str, str] = {
    "explanatory": "Explanatory",
    "minimal": "Minimal",
    "none": "No comments",
}

THEMES: dict[str, str] = {"dark": "Dark", "light": "Light", "system": "Follow system"}


@dataclass
class Preferences:
    shell: str = "bash"
    comment_style: str = "explanatory"
    strict_mode: bool = True
    color_output: bool = True
    backup_before_overwrite: bool = True
    #: Folder for backups; empty = next to the original file.
    backup_directory: str = ""
    open_after_generate: bool = True
    output_directory: str = "~/Scripts"
    executable_permissions: bool = True
    theme: str = "dark"

    editor_tab_width: int = 4
    editor_insert_spaces: bool = True
    editor_show_line_numbers: bool = True
    editor_highlight_current_line: bool = True
    editor_wrap_text: bool = False
    editor_show_right_margin: bool = True
    editor_font_size: int = 11

    completion_enabled: bool = True
    completion_while_typing: bool = True
    completion_min_chars: int = 2
    completion_system_commands: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["version"] = SCHEMA_VERSION
        return data

    def output_path(self) -> Path:
        return expand_user_path(self.output_directory)

    def backup_path(self) -> Path | None:
        return expand_user_path(self.backup_directory) if self.backup_directory.strip() else None


_CHOICES: dict[str, dict[str, str]] = {
    "shell": SUPPORTED_SHELLS,
    "comment_style": COMMENT_STYLES,
    "theme": THEMES,
}

_INT_RANGES: dict[str, tuple[int, int]] = {
    "editor_tab_width": (1, 16),
    "editor_font_size": (6, 48),
    "completion_min_chars": (1, 10),
}


def validate_field(name: str, value: Any) -> Any:
    """Validate a value and return it normalized; raise ``ValueError`` if invalid."""
    default = getattr(Preferences(), name)
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise ValueError(f"{name} must be true or false")
        return value
    if isinstance(default, int):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{name} must be an integer")
        low, high = _INT_RANGES[name]
        if not low <= value <= high:
            raise ValueError(f"{name} must be between {low} and {high}")
        return value
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    if name in _CHOICES and value not in _CHOICES[name]:
        allowed = ", ".join(_CHOICES[name])
        raise ValueError(f"{name}={value!r} is not valid (options: {allowed})")
    if name == "output_directory":
        expand_user_path(value)  # rejects empty paths
        return value.strip()
    if name == "backup_directory":
        return value.strip()
    return value


def preferences_from_dict(data: Any) -> tuple[Preferences, list[str]]:
    """Build valid preferences from arbitrary data.

    Missing or invalid fields fall back to their defaults; the list of warnings
    is returned so the UI can tell the user.
    """
    prefs = Preferences()
    warnings: list[str] = []
    if not isinstance(data, dict):
        return prefs, ["The preferences file does not contain a JSON object; using defaults"]
    for field in fields(Preferences):
        if field.name not in data:
            continue
        try:
            setattr(prefs, field.name, validate_field(field.name, data[field.name]))
        except ValueError as exc:
            warnings.append(f"{exc}; using the default value")
    return prefs, warnings
