"""Generator models: parameters, templates and generation settings."""

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
    #: Comma-separated list of values.
    LIST = "list"


class RiskLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def label(self) -> str:
        return {"low": "Low risk", "medium": "Medium risk", "high": "High risk"}[self.value]


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
    #: Extra check: returns an error message or ``None``.
    check: Callable[[Any], str | None] | None = None

    def coerce(self, raw: Any) -> Any:
        """Convert a UI value to the parameter type (``ValueError`` if invalid)."""
        if self.type is ParamType.BOOL:
            if isinstance(raw, bool):
                return raw
            if isinstance(raw, str) and raw.strip().lower() in {"true", "false", "1", "0", "yes", "no"}:
                return raw.strip().lower() in {"true", "1", "yes"}
            raise ValueError("Must be yes or no")

        if self.type is ParamType.INT:
            if isinstance(raw, bool):
                raise ValueError("Must be an integer")
            try:
                value = int(str(raw).strip())
            except ValueError:
                raise ValueError("Must be an integer") from None
            if self.minimum is not None and value < self.minimum:
                raise ValueError(f"The minimum is {self.minimum}")
            if self.maximum is not None and value > self.maximum:
                raise ValueError(f"The maximum is {self.maximum}")
            return value

        if self.type is ParamType.LIST:
            items = raw if isinstance(raw, (list, tuple)) else str(raw).split(",")
            values = [str(item).strip() for item in items if str(item).strip()]
            if self.required and not values:
                raise ValueError("Enter at least one value")
            return values

        if not isinstance(raw, str):
            raise ValueError("Must be text")
        value = raw.strip()
        if self.required and not value:
            raise ValueError("This field is required")
        if "\n" in value or "\0" in value:
            raise ValueError("Cannot contain line breaks")
        if self.type is ParamType.CHOICE and value not in {key for key, _ in self.choices}:
            raise ValueError("Invalid option")
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
    """Fragments a template contributes to the script; the engine assembles them."""

    #: Global variables and constants.
    globals: list[str] = field(default_factory=list)
    #: Helper functions (each item is a complete function).
    functions: list[str] = field(default_factory=list)
    #: Body of ``main``, unindented.
    main: list[str] = field(default_factory=list)
    uses_logging: bool = False
    #: Usage line for the header when it depends on the parameters.
    usage: str | None = None


@dataclass(frozen=True)
class Template:
    id: str
    name: str
    description: str
    category: str
    #: Default description of the generated script.
    script_description: str
    usage: str = ""
    params: tuple[TemplateParam, ...] = ()
    risk: Risk = Risk(RiskLevel.LOW)
    build: Callable[[BuildContext], TemplateBody] | None = None
    #: Validation across several parameters: returns ``{key: error}``.
    cross_check: Callable[[Mapping[str, Any]], dict[str, str]] | None = None
    #: Literal code of a custom template (replaces ``build``).
    custom_body: str | None = None
    tags: tuple[str, ...] = ()

    @property
    def is_custom(self) -> bool:
        return self.custom_body is not None

    def default_values(self) -> dict[str, Any]:
        return {p.key: p.default for p in self.params}

    def validate(self, raw_values: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
        """Return ``(values, errors)``; missing parameters take their default value."""
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
