"""Modelo de preferencias y su validación."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from mkscript_studio.paths import expand_user_path

SCHEMA_VERSION = 1

#: Shells cuya generación está implementada. Zsh y POSIX sh se anunciarán
#: aquí cuando el motor sepa producir código correcto para ellos.
SUPPORTED_SHELLS: dict[str, str] = {"bash": "Bash"}

COMMENT_STYLES: dict[str, str] = {
    "explanatory": "Explicativos",
    "minimal": "Mínimos",
    "none": "Sin comentarios",
}

THEMES: dict[str, str] = {"dark": "Oscuro", "light": "Claro", "system": "Según el sistema"}


@dataclass
class Preferences:
    shell: str = "bash"
    comment_style: str = "explanatory"
    strict_mode: bool = True
    color_output: bool = True
    backup_before_overwrite: bool = True
    #: Carpeta para las copias; vacío = junto al archivo original.
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
    """Valida un valor y lo devuelve normalizado; lanza ``ValueError`` si no es válido."""
    default = getattr(Preferences(), name)
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise ValueError(f"{name} debe ser verdadero o falso")
        return value
    if isinstance(default, int):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{name} debe ser un número entero")
        low, high = _INT_RANGES[name]
        if not low <= value <= high:
            raise ValueError(f"{name} debe estar entre {low} y {high}")
        return value
    if not isinstance(value, str):
        raise ValueError(f"{name} debe ser texto")
    if name in _CHOICES and value not in _CHOICES[name]:
        allowed = ", ".join(_CHOICES[name])
        raise ValueError(f"{name}={value!r} no es válido (opciones: {allowed})")
    if name == "output_directory":
        expand_user_path(value)  # rechaza rutas vacías
        return value.strip()
    if name == "backup_directory":
        return value.strip()
    return value


def preferences_from_dict(data: Any) -> tuple[Preferences, list[str]]:
    """Construye unas preferencias válidas a partir de datos arbitrarios.

    Los campos ausentes o inválidos toman su valor predeterminado; se devuelve
    la lista de avisos para que la interfaz pueda informar al usuario.
    """
    prefs = Preferences()
    warnings: list[str] = []
    if not isinstance(data, dict):
        return prefs, ["El archivo de preferencias no contiene un objeto JSON; se usan los valores predeterminados"]
    for field in fields(Preferences):
        if field.name not in data:
            continue
        try:
            setattr(prefs, field.name, validate_field(field.name, data[field.name]))
        except ValueError as exc:
            warnings.append(f"{exc}; se usa el valor predeterminado")
    return prefs, warnings
