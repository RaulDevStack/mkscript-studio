"""Motor de generación de scripts Bash, independiente de la interfaz gráfica."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from mkscript_studio.generator.models import GenerationConfig, Template, TemplateBody


class GenerationError(ValueError):
    def __init__(self, message: str, errors: Mapping[str, str] | None = None) -> None:
        super().__init__(message)
        self.errors = dict(errors or {})


@dataclass(frozen=True)
class BuildContext:
    values: Mapping[str, Any]
    config: GenerationConfig

    @property
    def explanatory(self) -> bool:
        return self.config.comment_style == "explanatory"

    @property
    def strict(self) -> bool:
        return self.config.strict_mode

    def explain(self, *lines: str) -> list[str]:
        """Comentarios que solo aparecen con el estilo «explicativo»."""
        return [f"# {line}" if line else "#" for line in lines] if self.explanatory else []

    def note(self, *lines: str) -> list[str]:
        """Comentarios breves, presentes salvo con el estilo «sin comentarios»."""
        if self.config.comment_style == "none":
            return []
        return [f"# {line}" if line else "#" for line in lines]


_STRICT_EXPLANATION = (
    "Modo estricto:",
    "  -E           las trampas ERR se heredan en funciones y subshells",
    "  -e           el script termina si un comando falla (salvo en if, while, && y ||)",
    "  -u           usar una variable sin definir es un error",
    "  -o pipefail  una tubería falla si falla cualquiera de sus comandos",
)


def _logging_block(ctx: BuildContext) -> list[str]:
    lines = ctx.explain("Mensajes de estado en la salida de error, para no mezclarlos con los datos.")
    if ctx.config.color_output:
        lines += ctx.explain("Los colores solo se usan en una terminal y se desactivan con NO_COLOR.")
        lines += [
            'if [[ -t 2 && -z "${NO_COLOR:-}" ]]; then',
            "    readonly C_RED=$'\\033[0;31m' C_GREEN=$'\\033[0;32m' C_YELLOW=$'\\033[0;33m' C_BLUE=$'\\033[0;34m' C_RESET=$'\\033[0m'",
            "else",
            "    readonly C_RED='' C_GREEN='' C_YELLOW='' C_BLUE='' C_RESET=''",
            "fi",
            "",
            "info() { printf '%s[*]%s %s\\n' \"${C_BLUE}\" \"${C_RESET}\" \"$*\" >&2; }",
            "ok()   { printf '%s[✓]%s %s\\n' \"${C_GREEN}\" \"${C_RESET}\" \"$*\" >&2; }",
            "warn() { printf '%s[!]%s %s\\n' \"${C_YELLOW}\" \"${C_RESET}\" \"$*\" >&2; }",
            "die()  { printf '%s[X]%s %s\\n' \"${C_RED}\" \"${C_RESET}\" \"$*\" >&2; exit 1; }",
        ]
    else:
        lines += [
            "info() { printf '[*] %s\\n' \"$*\" >&2; }",
            "ok()   { printf '[✓] %s\\n' \"$*\" >&2; }",
            "warn() { printf '[!] %s\\n' \"$*\" >&2; }",
            "die()  { printf '[X] %s\\n' \"$*\" >&2; exit 1; }",
        ]
    return lines


_LEADING_SPACES_RE = re.compile(r"^( +)")


def _reindent(line: str, indent: str) -> str:
    """Las plantillas se escriben con 4 espacios; se adaptan a la preferencia del usuario."""
    if indent == "    ":
        return line
    match = _LEADING_SPACES_RE.match(line)
    if not match:
        return line
    levels, rest = divmod(len(match.group(1)), 4)
    return indent * levels + " " * rest + line[match.end():]


def _header(template: Template, config: GenerationConfig, description: str, usage: str) -> list[str]:
    if config.comment_style == "none":
        return []
    usage = f" {usage}" if usage else ""
    lines = [
        f"# {config.script_name}",
        f"# Descripción: {description}",
        f"# Uso: ./{config.script_name}{usage}",
    ]
    if config.comment_style == "explanatory":
        lines.append(f"# Generado con MKSCRIPT Studio (plantilla: {template.name}).")
    return lines + [""]


def _render_custom(template: Template, config: GenerationConfig, description: str) -> str:
    assert template.custom_body is not None
    text = template.custom_body.replace("{{script_name}}", config.script_name)
    text = text.replace("{{description}}", description)
    return text if text.endswith("\n") else text + "\n"


def _single_line(text: str) -> str:
    return " ".join(text.split())


def generate_script(template: Template, config: GenerationConfig, raw_values: Mapping[str, Any] | None = None) -> str:
    """Genera el código completo; el resultado es determinista para la misma entrada."""
    if config.shell != "bash":
        raise GenerationError(f"La generación para «{config.shell}» no está implementada; solo Bash")
    if not config.script_name.strip():
        raise GenerationError("Falta el nombre del script")

    description = _single_line(config.description) or template.script_description
    if template.is_custom:
        return _render_custom(template, config, description)

    values, errors = template.validate(raw_values or {})
    if errors:
        raise GenerationError("Hay parámetros no válidos", errors)

    ctx = BuildContext(values=values, config=config)
    assert template.build is not None
    body: TemplateBody = template.build(ctx)

    lines: list[str] = ["#!/usr/bin/env bash", ""]
    lines += _header(template, config, description, body.usage if body.usage is not None else template.usage)

    if config.strict_mode:
        lines += ctx.explain(*_STRICT_EXPLANATION)
        lines += ["set -Eeuo pipefail", ""]
    else:
        lines += ctx.explain("Modo estricto desactivado: un comando fallido no detiene el script.")
        if ctx.explanatory:
            lines.append("")

    if body.uses_logging:
        lines += _logging_block(ctx) + [""]
    if body.globals:
        lines += body.globals + [""]
    for function in body.functions:
        lines += function.rstrip("\n").split("\n") + [""]

    lines.append("main() {")
    lines += [f"    {line}" if line else "" for line in body.main]
    lines += ["}", "", 'main "$@"']

    return _reindent_code(lines, config.indent)


_HEREDOC_RE = re.compile(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?")


def _reindent_code(lines: list[str], indent: str) -> str:
    """Aplica la indentación preferida sin tocar el contenido de los heredocs."""
    output: list[str] = []
    delimiter: str | None = None
    for line in lines:
        if delimiter is not None:
            output.append(line)
            if line.strip() == delimiter:
                delimiter = None
            continue
        output.append(_reindent(line, indent))
        match = _HEREDOC_RE.search(line)
        if match:
            delimiter = match.group(1)
    return "\n".join(output) + "\n"
