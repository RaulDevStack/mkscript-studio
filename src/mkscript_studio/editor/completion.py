"""Motor de autocompletado para Bash, independiente de GTK.

El contenido del editor solo se analiza como texto: nunca se ejecuta.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from mkscript_studio.editor.bash_words import (
    BUILTINS,
    COMMAND_PREFIX_WORDS,
    COMMANDS,
    KEYWORDS,
    POPULARITY,
    WELL_KNOWN_VARIABLES,
)


class CompletionKind(Enum):
    FUNCTION = "función"
    KEYWORD = "palabra reservada"
    BUILTIN = "builtin"
    COMMAND = "orden"
    SYSTEM_COMMAND = "orden del sistema"
    VARIABLE = "variable"
    PATH = "ruta"


_KIND_ORDER = {
    CompletionKind.FUNCTION: 0,
    CompletionKind.VARIABLE: 0,
    CompletionKind.PATH: 0,
    CompletionKind.BUILTIN: 1,
    CompletionKind.KEYWORD: 2,
    CompletionKind.COMMAND: 3,
    CompletionKind.SYSTEM_COMMAND: 4,
}
_POPULARITY_INDEX = {word: index for index, word in enumerate(POPULARITY)}


class ContextKind(Enum):
    NONE = "none"
    COMMAND = "command"
    ARGUMENT = "argument"
    VARIABLE = "variable"
    PATH = "path"


@dataclass(frozen=True)
class Candidate:
    label: str
    #: Texto que sustituye al prefijo (``context.start``..``context.end``).
    insert_text: str
    kind: CompletionKind
    detail: str = ""


@dataclass(frozen=True)
class CompletionContext:
    kind: ContextKind
    prefix: str = ""
    start: int = 0
    end: int = 0
    #: Longitud del nombre escrito (sin «$», «${» ni la parte de directorio).
    name_length: int = 0


_VARIABLE_RE = re.compile(r"\$(\{?)([A-Za-z_][A-Za-z0-9_]*)?$")
_TOKEN_RE = re.compile(r"[^\s;&|()<>`'\"$={}]*$")
_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
_FUNCTION_RE = re.compile(
    r"^[ \t]*(?:function[ \t]+([A-Za-z_][\w:.-]*)|([A-Za-z_][\w:.-]*)[ \t]*\([ \t]*\))", re.MULTILINE
)
_ASSIGNMENT_RE = re.compile(rf"(?:^|[\s;&|(])({_IDENTIFIER})(?:\[[^\]\n]*\])?\+?=", re.MULTILINE)
_DECLARATION_RE = re.compile(r"\b(?:local|declare|readonly|export|typeset)\b([^;&|\n]*)")
_FOR_RE = re.compile(rf"\b(?:for|select)[ \t]+({_IDENTIFIER})[ \t]+in\b")
_READ_RE = re.compile(r"\b(?:read|mapfile|readarray)\b([^;&|\n]*)")
_FOR_IN_RE = re.compile(rf"\b(?:for|select)\s+{_IDENTIFIER}$|\bcase\s+\S+$")


def _line_state(before: str) -> str:
    """Devuelve ``code``, ``single``, ``double`` o ``comment`` al final de ``before``."""
    state = "code"
    escaped = False
    previous = " "
    for char in before:
        if state == "comment":
            break
        if escaped:
            escaped = False
        elif char == "\\" and state != "single":
            escaped = True
        elif state == "code":
            if char == "'":
                state = "single"
            elif char == '"':
                state = "double"
            elif char == "#" and (previous.isspace() or previous in ";&|("):
                state = "comment"
        elif state == "single" and char == "'":
            state = "code"
        elif state == "double" and char == '"':
            state = "code"
        previous = char
    return state


def defined_functions(text: str) -> list[str]:
    names = []
    for match in _FUNCTION_RE.finditer(text):
        name = match.group(1) or match.group(2)
        if name not in names and name not in KEYWORDS:
            names.append(name)
    return names


def defined_variables(text: str) -> list[str]:
    names: list[str] = []

    def add(name: str) -> None:
        if re.fullmatch(_IDENTIFIER, name) and name not in names:
            names.append(name)

    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    for match in _ASSIGNMENT_RE.finditer(code):
        add(match.group(1))
    for match in _DECLARATION_RE.finditer(code):
        for word in match.group(1).split():
            if not word.startswith("-"):
                add(word.split("=", 1)[0])
    for match in _FOR_RE.finditer(code):
        add(match.group(1))
    for match in _READ_RE.finditer(code):
        # Los nombres de variable son las últimas palabras de la orden.
        for word in reversed(match.group(1).split()):
            if not re.fullmatch(_IDENTIFIER, word):
                break
            add(word)
    return names


def scan_path_commands(path_env: str | None = None, limit: int = 5000) -> list[str]:
    """Nombres de ejecutables del PATH (solo se listan; no se ejecutan)."""
    found: set[str] = set()
    for directory in (path_env if path_env is not None else os.environ.get("PATH", "")).split(os.pathsep):
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if len(found) >= limit:
                        return sorted(found)
                    try:
                        if entry.is_file() and os.access(entry.path, os.X_OK):
                            found.add(entry.name)
                    except OSError:
                        continue
        except OSError:
            continue
    return sorted(found)


@dataclass
class CompletionEngine:
    #: Proveedor perezoso de órdenes del sistema (``None`` = desactivado).
    system_commands: Callable[[], Iterable[str]] | None = None
    #: Directorio para completar rutas relativas (el del documento).
    base_directory: Path | None = None
    max_results: int = 60
    max_directory_entries: int = 500
    _system_cache: list[str] | None = field(default=None, init=False, repr=False)

    # -- contexto ----------------------------------------------------------

    def context_at(self, text: str, offset: int) -> CompletionContext:
        offset = max(0, min(offset, len(text)))
        line_start = text.rfind("\n", 0, offset) + 1
        before = text[line_start:offset]
        state = _line_state(before)
        if state in ("comment", "single"):
            return CompletionContext(ContextKind.NONE, start=offset, end=offset)

        variable = _VARIABLE_RE.search(before)
        if variable:
            token = variable.group(0)
            return CompletionContext(ContextKind.VARIABLE, token, offset - len(token), offset,
                                     len(variable.group(2) or ""))
        if state == "double":
            return CompletionContext(ContextKind.NONE, start=offset, end=offset)

        token = _TOKEN_RE.search(before).group(0)
        start = offset - len(token)
        if "/" in token or token.startswith("~"):
            name = token.rsplit("/", 1)[-1] if "/" in token else ""
            return CompletionContext(ContextKind.PATH, token, start, offset, len(name))

        preceding = before[: len(before) - len(token)].rstrip()
        last_word = preceding.split()[-1] if preceding.split() else ""
        # «)» abre una orden tras un patrón de case, pero no tras $(…) ni ((…)).
        closes_case_pattern = preceding.endswith(")") and "$(" not in preceding and "((" not in preceding
        is_command = (
            not preceding
            or preceding[-1] in ";|&({`!"
            or closes_case_pattern
            or preceding.endswith("$(")
            or last_word in COMMAND_PREFIX_WORDS
        )
        kind = ContextKind.COMMAND if is_command else ContextKind.ARGUMENT
        return CompletionContext(kind, token, start, offset, len(token))

    # -- candidatos --------------------------------------------------------

    def complete(self, text: str, offset: int, explicit: bool = True, min_chars: int = 1) -> tuple[CompletionContext, list[Candidate]]:
        """Candidatos en ``offset``. Con ``explicit=False`` (mientras se escribe) se exige ``min_chars``."""
        context = self.context_at(text, offset)
        if context.kind is ContextKind.NONE:
            return context, []
        if not explicit and context.name_length < min_chars:
            return context, []
        # El análisis del archivo ignora la palabra en curso para no sugerirla a sí misma.
        word_end = offset
        while word_end < len(text) and (text[word_end].isalnum() or text[word_end] in "_-."):
            word_end += 1
        rest = text[: context.start] + text[word_end:]

        if context.kind is ContextKind.VARIABLE:
            candidates = self._variables(context, rest, text[offset: offset + 1])
        elif context.kind is ContextKind.PATH:
            candidates = self._paths(context)
        elif context.kind is ContextKind.COMMAND:
            if not context.prefix and not explicit:
                return context, []
            candidates = self._commands(context.prefix, rest)
        else:
            candidates = self._arguments(context, text[: context.start])
        return context, candidates[: self.max_results]

    def _commands(self, prefix: str, rest: str) -> list[Candidate]:
        pool: dict[str, Candidate] = {}

        def add(name: str, kind: CompletionKind, detail: str = "") -> None:
            if name.startswith(prefix) and name not in pool:
                pool[name] = Candidate(name, name, kind, detail)

        for name in defined_functions(rest):
            add(name, CompletionKind.FUNCTION, "definida en este archivo")
        for name, detail in BUILTINS.items():
            add(name, CompletionKind.BUILTIN, detail)
        for name, detail in KEYWORDS.items():
            if name != "in":
                add(name, CompletionKind.KEYWORD, detail)
        for name, detail in COMMANDS.items():
            add(name, CompletionKind.COMMAND, detail)
        if self.system_commands is not None and prefix:
            if self._system_cache is None:
                self._system_cache = sorted(set(self.system_commands()))
            for name in self._system_cache:
                add(name, CompletionKind.SYSTEM_COMMAND)
        return sorted(pool.values(), key=_rank)

    def _arguments(self, context: CompletionContext, before: str) -> list[Candidate]:
        line = before[before.rfind("\n") + 1:].rstrip()
        if _FOR_IN_RE.search(line) and "in".startswith(context.prefix):
            return [Candidate("in", "in", CompletionKind.KEYWORD, KEYWORDS["in"])]
        return []

    def _variables(self, context: CompletionContext, rest: str, next_char: str) -> list[Candidate]:
        braced = context.prefix.startswith("${")
        name_prefix = context.prefix[2:] if braced else context.prefix[1:]
        names: dict[str, str] = {name: "definida en este archivo" for name in defined_variables(rest)}
        for name, detail in WELL_KNOWN_VARIABLES.items():
            names.setdefault(name, detail)
        result = []
        for index, (name, detail) in enumerate(names.items()):
            if not name.startswith(name_prefix):
                continue
            if braced:
                insert = "${" + name + ("" if next_char == "}" else "}")
            else:
                insert = "$" + name
            result.append((index, Candidate(name, insert, CompletionKind.VARIABLE, detail)))
        # Primero las del archivo (en orden de aparición), luego las conocidas.
        return [candidate for _, candidate in sorted(result, key=lambda item: item[0])]

    def _paths(self, context: CompletionContext) -> list[Candidate]:
        token = context.prefix
        if "/" not in token:
            return [Candidate("~/", "~/", CompletionKind.PATH, "carpeta personal")] if token == "~" else []
        directory_part, name_prefix = token.rsplit("/", 1)
        directory_part += "/"
        if directory_part.startswith("~"):
            if not (directory_part == "~/" or directory_part.startswith("~/")):
                return []  # ~usuario no se expande
            directory = Path.home() / directory_part[2:]
        elif directory_part.startswith("/"):
            directory = Path(directory_part)
        elif self.base_directory is not None:
            directory = self.base_directory / directory_part
        else:
            return []
        candidates = []
        try:
            with os.scandir(directory) as entries:
                for count, entry in enumerate(entries):
                    if count >= self.max_directory_entries:
                        break
                    if not entry.name.startswith(name_prefix):
                        continue
                    if entry.name.startswith(".") and not name_prefix.startswith("."):
                        continue
                    try:
                        is_dir = entry.is_dir()
                    except OSError:
                        is_dir = False
                    suffix = "/" if is_dir else ""
                    candidates.append(Candidate(entry.name + suffix, directory_part + entry.name + suffix,
                                                CompletionKind.PATH, "carpeta" if is_dir else "archivo"))
        except OSError:
            return []
        return sorted(candidates, key=lambda c: (not c.label.endswith("/"), c.label.lower()))


def _rank(candidate: Candidate) -> tuple:
    return (
        0 if candidate.kind is CompletionKind.FUNCTION else 1,
        _POPULARITY_INDEX.get(candidate.label, len(_POPULARITY_INDEX)),
        _KIND_ORDER[candidate.kind],
        len(candidate.label),
        candidate.label,
    )


# ---------------------------------------------------------------------------
# Comportamiento de la tecla Tab
# ---------------------------------------------------------------------------

class TabActionKind(Enum):
    #: No hay nada que completar: el editor debe indentar como siempre.
    INDENT = "indent"
    #: Una única coincidencia: sustituir el prefijo directamente.
    COMPLETE = "complete"
    #: Varias coincidencias: (opcionalmente ampliar el prefijo común y) mostrar la lista.
    SHOW = "show"


@dataclass(frozen=True)
class TabAction:
    kind: TabActionKind
    start: int = 0
    end: int = 0
    text: str = ""
    candidates: tuple[Candidate, ...] = ()


def common_prefix(values: list[str]) -> str:
    return os.path.commonprefix(values) if values else ""


def resolve_tab(engine: CompletionEngine, text: str, offset: int) -> TabAction:
    """Decide qué hace Tab en ``offset``; solo se sustituye el prefijo anterior al cursor."""
    context, candidates = engine.complete(text, offset, explicit=True)
    if context.kind is ContextKind.NONE or not context.prefix or not candidates:
        return TabAction(TabActionKind.INDENT)
    if len(candidates) == 1:
        only = candidates[0]
        if only.insert_text == context.prefix:
            return TabAction(TabActionKind.INDENT)
        return TabAction(TabActionKind.COMPLETE, context.start, context.end, only.insert_text, (only,))
    shared = common_prefix([c.insert_text for c in candidates])
    text_to_insert = shared if len(shared) > len(context.prefix) else context.prefix
    return TabAction(TabActionKind.SHOW, context.start, context.end, text_to_insert, tuple(candidates))
