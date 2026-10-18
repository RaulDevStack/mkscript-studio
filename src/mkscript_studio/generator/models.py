"""Modelos del generador: parámetros, plantillas y configuración de generación."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mkscript_studio.generator.engine import BuildContext
    from mkscript_studio.preferences.models import Preferences


class ParamType(Enum):
    TEXT = "text"
    PATH = "path"
    INT = "int"
    BOOL = "bool"
    CHOICE = "choice"
    #: Lista de valores separados por comas.
    LIST = "list"


class RiskLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def label(self) -> str:
        return {"low": "Riesgo bajo", "medium": "Riesgo medio", "high": "Riesgo alto"}[self.value]


@dataclass(frozen=True)
class Risk:
    level: RiskLevel
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class TemplateParam:
    key: str
    label: str
    type: ParamType
    default: Any
    description: str = ""
    required: bool = True
    choices: tuple[tuple[str, str], ...] = ()
    minimum: int | None = None
    maximum: int | None = None
    #: Comprobación adicional: devuelve un mensaje de error o ``None``.
    check: Callable[[Any], str | None] | None = None

    def coerce(self, raw: Any) -> Any:
        """Convierte un valor de la interfaz al tipo del parámetro (``ValueError`` si no es válido)."""
        if self.type is ParamType.BOOL:
            if isinstance(raw, bool):
                return raw
            if isinstance(raw, str) and raw.strip().lower() in {"true", "false", "1", "0", "sí", "si", "no"}:
                return raw.strip().lower() in {"true", "1", "sí", "si"}
            raise ValueError("Debe ser sí o no")

        if self.type is ParamType.INT:
            if isinstance(raw, bool):
                raise ValueError("Debe ser un número entero")
            try:
                value = int(str(raw).strip())
            except ValueError:
                raise ValueError("Debe ser un número entero") from None
            if self.minimum is not None and value < self.minimum:
                raise ValueError(f"El mínimo es {self.minimum}")
            if self.maximum is not None and value > self.maximum:
                raise ValueError(f"El máximo es {self.maximum}")
            return value

        if self.type is ParamType.LIST:
            items = raw if isinstance(raw, (list, tuple)) else str(raw).split(",")
            values = [str(item).strip() for item in items if str(item).strip()]
            if self.required and not values:
                raise ValueError("Indica al menos un valor")
            return values

        if not isinstance(raw, str):
            raise ValueError("Debe ser texto")
        value = raw.strip()
        if self.required and not value:
            raise ValueError("Este campo es obligatorio")
        if "\n" in value or "\0" in value:
            raise ValueError("No puede contener saltos de línea")
        if self.type is ParamType.CHOICE and value not in {key for key, _ in self.choices}:
            raise ValueError("Opción no válida")
        return value

    def validate(self, raw: Any) -> tuple[Any, str | None]:
        try:
            value = self.coerce(raw)
        except ValueError as exc:
            return None, str(exc)
        if self.check is not None:
            message = self.check(value)
            if message:
                return value, message
        return value, None


@dataclass
class TemplateBody:
    """Fragmentos que una plantilla aporta al script; el motor los ensambla."""

    #: Variables y constantes globales.
    globals: list[str] = field(default_factory=list)
    #: Funciones auxiliares (cada elemento es una función completa).
    functions: list[str] = field(default_factory=list)
    #: Cuerpo de ``main`` sin indentar.
    main: list[str] = field(default_factory=list)
    uses_logging: bool = False
    #: Línea de uso para la cabecera si depende de los parámetros.
    usage: str | None = None


@dataclass(frozen=True)
class Template:
    id: str
    name: str
    description: str
    category: str
    #: Descripción por defecto del script generado.
    script_description: str
    usage: str = ""
    params: tuple[TemplateParam, ...] = ()
    risk: Risk = Risk(RiskLevel.LOW)
    build: Callable[[BuildContext], TemplateBody] | None = None
    #: Validación que relaciona varios parámetros: devuelve ``{clave: error}``.
    cross_check: Callable[[Mapping[str, Any]], dict[str, str]] | None = None
    #: Código literal de una plantilla personalizada (sustituye a ``build``).
    custom_body: str | None = None
    tags: tuple[str, ...] = ()

    @property
    def is_custom(self) -> bool:
        return self.custom_body is not None

    def default_values(self) -> dict[str, Any]:
        return {p.key: p.default for p in self.params}

    def validate(self, raw_values: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
        """Devuelve ``(valores, errores)``; los parámetros ausentes toman su valor por defecto."""
        values: dict[str, Any] = {}
        errors: dict[str, str] = {}
        for param in self.params:
            value, error = param.validate(raw_values.get(param.key, param.default))
            if error:
                errors[param.key] = error
            else:
                values[param.key] = value
        if not errors and self.cross_check is not None:
            errors.update(self.cross_check(values))
        return values, errors


@dataclass(frozen=True)
class GenerationConfig:
    script_name: str
    description: str = ""
    shell: str = "bash"
    comment_style: str = "explanatory"
    strict_mode: bool = True
    color_output: bool = True
    indent: str = "    "

    @classmethod
    def from_preferences(cls, prefs: Preferences, script_name: str, description: str = "") -> GenerationConfig:
        indent = " " * prefs.editor_tab_width if prefs.editor_insert_spaces else "\t"
        return cls(
            script_name=script_name,
            description=description,
            shell=prefs.shell,
            comment_style=prefs.comment_style,
            strict_mode=prefs.strict_mode,
            color_output=prefs.color_output,
            indent=indent,
        )


IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
