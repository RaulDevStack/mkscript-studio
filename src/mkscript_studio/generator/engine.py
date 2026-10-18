"""Bash script generation engine, independent of the GUI."""

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
        """Comments that only appear with the "explanatory" style."""
        return [f"# {line}" if line else "#" for line in lines] if self.explanatory else []

    def note(self, *lines: str) -> list[str]:
        """Short comments, present unless the style is "no comments"."""
        if self.config.comment_style == "none":
            return []
        return [f"# {line}" if line else "#" for line in lines]


_STRICT_EXPLANATION = (
    "Strict mode:",
    "  -E           ERR traps are inherited by functions and subshells",
    "  -e           exit when a command fails (except in if, while, && and ||)",
    "  -u           using an unset variable is an error",
    "  -o pipefail  a pipeline fails if any of its commands fails",
)


def _logging_block(ctx: BuildContext) -> list[str]:
    lines = ctx.explain("Status messages go to stderr so they do not mix with the data.")
    if ctx.config.color_output:
        lines += ctx.explain("Colors are only used on a terminal and can be disabled with NO_COLOR.")
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
    """Templates are written with 4 spaces; adapt them to the user's preference."""
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
        f"# Description: {description}",
        f"# Usage: ./{config.script_name}{usage}",
    ]
    if config.comment_style == "explanatory":
        lines.append(f"# Generated with MKSCRIPT Studio (template: {template.name}).")
    return lines + [""]


def _render_custom(template: Template, config: GenerationConfig, description: str) -> str:
    assert template.custom_body is not None
    text = template.custom_body.replace("{{script_name}}", config.script_name)
    text = text.replace("{{description}}", description)
    return text if text.endswith("\n") else text + "\n"


def _single_line(text: str) -> str:
    return " ".join(text.split())


def generate_script(template: Template, config: GenerationConfig, raw_values: Mapping[str, Any] | None = None) -> str:
    """Generate the full code; the output is deterministic for the same input."""
    if config.shell != "bash":
        raise GenerationError(f"Generation for '{config.shell}' is not implemented; only Bash is supported")
    if not config.script_name.strip():
        raise GenerationError("The script name is missing")

    description = _single_line(config.description) or template.script_description
    if template.is_custom:
        return _render_custom(template, config, description)

    values, errors = template.validate(raw_values or {})
    if errors:
        raise GenerationError("Some parameters are invalid", errors)

    ctx = BuildContext(values=values, config=config)
    assert template.build is not None
    body: TemplateBody = template.build(ctx)

    lines: list[str] = ["#!/usr/bin/env bash", ""]
    lines += _header(template, config, description, body.usage if body.usage is not None else template.usage)

    if config.strict_mode:
        lines += ctx.explain(*_STRICT_EXPLANATION)
        lines += ["set -Eeuo pipefail", ""]
    else:
        lines += ctx.explain("Strict mode disabled: a failing command does not stop the script.")
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
    """Apply the preferred indentation without touching heredoc contents."""
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
