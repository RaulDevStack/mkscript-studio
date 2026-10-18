"""Plantillas integradas y registro de plantillas (integradas + personalizadas).

Las plantillas no ejecutan nada al generarse: solo producen texto. Las que
pueden modificar datos funcionan en modo simulación salvo que el script
generado reciba ``--apply`` de forma explícita.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from mkscript_studio.generator.bash import (
    array_literal,
    is_absolute_or_home,
    path_key,
    path_literal,
    single_quote,
)
from mkscript_studio.generator.engine import BuildContext
from mkscript_studio.generator.models import (
    ParamType,
    Risk,
    RiskLevel,
    Template,
    TemplateBody,
    TemplateParam,
)
from mkscript_studio.persistence import read_json, write_json

CUSTOM_CATEGORY = "Personalizadas"

# ---------------------------------------------------------------------------
# Comprobaciones reutilizables
# ---------------------------------------------------------------------------

_PROTECTED_PATHS = {"/", "~", "/bin", "/boot", "/dev", "/etc", "/home", "/lib", "/lib64",
                    "/opt", "/proc", "/root", "/sbin", "/sys", "/usr", "/var"}


def _check_path(value: str) -> str | None:
    if not is_absolute_or_home(value):
        return "Usa una ruta absoluta o que empiece por ~/"
    return None


def _check_modifiable_path(value: str) -> str | None:
    error = _check_path(value)
    if error:
        return error
    if path_key(value) in _PROTECTED_PATHS:
        return "Por seguridad no se admite esta carpeta del sistema o la carpeta personal completa"
    return None


def _regex_check(pattern: str, message: str):
    compiled = re.compile(pattern)

    def check(value: Any) -> str | None:
        items = value if isinstance(value, list) else [value]
        bad = [item for item in items if not compiled.fullmatch(item)]
        return f"{message}: {', '.join(bad)}" if bad else None

    return check


def _parse_args_function(options: list[tuple[str, str, str]], extra_cases: Iterable[str] = ()) -> str:
    """Bucle de análisis de argumentos con -h/--help y las opciones indicadas.

    ``options`` contiene tuplas ``(patrón case, variable, valor)``.
    """
    lines = [
        "parse_args() {",
        "    while [[ $# -gt 0 ]]; do",
        '        case "$1" in',
        "            -h|--help) usage; exit 0 ;;",
    ]
    for pattern, variable, value in options:
        lines.append(f"            {pattern}) {variable}={value} ;;")
    lines += [f"            {case}" for case in extra_cases]
    lines += [
        '            *) usage >&2; die "Argumento desconocido: $1" ;;',
        "        esac",
        "        shift",
        "    done",
        "}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 1. Script mínimo
# ---------------------------------------------------------------------------

def _build_minimal(ctx: BuildContext) -> TemplateBody:
    main = ctx.explain("Escribe aquí la lógica principal. Los argumentos están en \"$@\".")
    main.append(f"echo {single_quote(ctx.values['message'])}")
    return TemplateBody(main=main)


MINIMAL = Template(
    id="minimal",
    name="Script mínimo",
    description="Punto de partida limpio: cabecera, modo estricto opcional y una función main.",
    category="Básicos",
    script_description="Muestra un mensaje de bienvenida.",
    usage="[argumentos]",
    params=(
        TemplateParam("message", "Mensaje", ParamType.TEXT, "Hola desde MKSCRIPT Studio",
                      "Texto que muestra el script con echo."),
    ),
    risk=Risk(RiskLevel.LOW, ("Solo muestra un mensaje; revisa los riesgos de la lógica que añadas.",)),
    build=_build_minimal,
    tags=("inicio", "main", "echo"),
)

# ---------------------------------------------------------------------------
# 2. Herramienta CLI con argumentos
# ---------------------------------------------------------------------------

def _flag_var(flag: str) -> str:
    # Los prefijos evitan pisar variables como PATH o HOME.
    return "OPT_" + flag.upper().replace("-", "_")


def _build_cli(ctx: BuildContext) -> TemplateBody:
    flags: list[str] = ctx.values["flags"]
    positional: str = ctx.values["positional"]
    required: bool = ctx.values["positional_required"]
    pos_var = "ARG_" + positional.upper()
    pos_usage = f"<{positional}>" if required else f"[{positional}]"

    globals_ = ['readonly SCRIPT_NAME="${0##*/}"', f"readonly VERSION={single_quote(ctx.values['version'])}", ""]
    globals_ += ctx.explain("Estado de las opciones; parse_args lo modifica.")
    globals_ += [f"{_flag_var(flag)}=false" for flag in flags]
    globals_.append(f'{pos_var}=""')

    width = max([len(f"--{flag}") for flag in flags] + [len("--version")])
    help_lines = [
        "usage() {",
        "    cat <<EOF",
        f"Uso: ${{SCRIPT_NAME}} [opciones] {pos_usage}",
        "",
        "Opciones:",
        f"  -h, {'--help'.ljust(width)}  Muestra esta ayuda y termina",
        f"  -V, {'--version'.ljust(width)}  Muestra la versión y termina",
    ]
    help_lines += [f"      {f'--{flag}'.ljust(width)}  Activa la opción «{flag}»" for flag in flags]
    help_lines += ["EOF", "}"]

    parse = [
        "parse_args() {",
        "    while [[ $# -gt 0 ]]; do",
        '        case "$1" in',
        "            -h|--help) usage; exit 0 ;;",
        "            -V|--version) printf '%s %s\\n' \"${SCRIPT_NAME}\" \"${VERSION}\"; exit 0 ;;",
    ]
    parse += [f"            --{flag}) {_flag_var(flag)}=true ;;" for flag in flags]
    parse += [
        "            --) shift; break ;;",
        '            -*) usage >&2; die "Opción desconocida: $1" ;;',
        "            *) break ;;",
        "        esac",
        "        shift",
        "    done",
        "",
    ]
    if required:
        parse += [
            "    if [[ $# -lt 1 ]]; then",
            "        usage >&2",
            f'        die "Falta el argumento {pos_usage}"',
            "    fi",
        ]
    parse += [
        f'    if [[ $# -gt 0 ]]; then {pos_var}="$1"; shift; fi',
        '    [[ $# -eq 0 ]] || die "Sobran argumentos: $*"',
        "}",
    ]

    main = ['parse_args "$@"', ""]
    main += ctx.explain("Sustituye estas líneas por la lógica de la herramienta.")
    main.append(f'info "{positional}: ${{{pos_var}:-(sin indicar)}}"')
    main += [f'info "--{flag}: ${{{_flag_var(flag)}}}"' for flag in flags]

    return TemplateBody(
        globals=globals_,
        functions=["\n".join(help_lines), "\n".join(parse)],
        main=main,
        uses_logging=True,
        usage=f"[opciones] {pos_usage}",
    )


def _check_flags(values: list[str]) -> str | None:
    error = _regex_check(r"[a-z][a-z0-9-]*", "Nombre de opción no válido (minúsculas, números y guiones)")(values)
    if error:
        return error
    reserved = {"help", "version"} & set(values)
    if reserved:
        return f"Opción reservada: {', '.join(sorted(reserved))}"
    if len(set(values)) != len(values):
        return "Hay opciones repetidas"
    return None


CLI_TOOL = Template(
    id="cli-tool",
    name="Herramienta CLI con argumentos",
    description="Análisis de opciones con --help y --version, opciones booleanas y un argumento posicional.",
    category="Básicos",
    script_description="Herramienta de línea de órdenes con opciones y ayuda integrada.",
    usage="[opciones] <argumento>",
    params=(
        TemplateParam("version", "Versión", ParamType.TEXT, "0.1.0", "Se muestra con --version.",
                      check=_regex_check(r"[0-9A-Za-z.+-]+", "Versión no válida")),
        TemplateParam("flags", "Opciones booleanas", ParamType.LIST, ["verbose", "dry-run"],
                      "Separadas por comas; cada una genera --opción.", required=False, check=_check_flags),
        TemplateParam("positional", "Argumento posicional", ParamType.TEXT, "archivo",
                      "Nombre del argumento (minúsculas).",
                      check=_regex_check(r"[a-z][a-z0-9_]*", "Usa minúsculas, números o _")),
        TemplateParam("positional_required", "Argumento obligatorio", ParamType.BOOL, True),
    ),
    risk=Risk(RiskLevel.LOW, (
        "El script generado solo analiza argumentos y muestra los valores recibidos.",
        "Revisa los riesgos de la lógica que añadas.",
    )),
    build=_build_cli,
    tags=("argumentos", "opciones", "getopts", "ayuda"),
)

# ---------------------------------------------------------------------------
# 3. Copia de seguridad
# ---------------------------------------------------------------------------

_COMPRESSION = {"gzip": ("z", ".tar.gz"), "xz": ("J", ".tar.xz"), "none": ("", ".tar")}


def _build_backup(ctx: BuildContext) -> TemplateBody:
    flag, extension = _COMPRESSION[ctx.values["compression"]]
    globals_ = [
        f"readonly SOURCE_DIR={path_literal(ctx.values['source_dir'])}",
        f"readonly DEST_DIR={path_literal(ctx.values['dest_dir'])}",
        f"readonly EXCLUDES={array_literal(ctx.values['excludes'])}",
    ]
    main = [
        'command -v tar >/dev/null 2>&1 || die "No se encontró el programa tar"',
        '[[ -d "${SOURCE_DIR}" ]] || die "No existe el directorio de origen: ${SOURCE_DIR}"',
        '[[ -r "${SOURCE_DIR}" ]] || die "No se puede leer el directorio de origen: ${SOURCE_DIR}"',
        'mkdir -p -- "${DEST_DIR}"',
        "",
        "local stamp archive",
        'stamp="$(date +%Y%m%d-%H%M%S)"',
        f'archive="${{DEST_DIR}}/$(basename -- "${{SOURCE_DIR}}")-${{stamp}}{extension}"',
    ]
    main += ctx.explain("Nunca se sobrescribe una copia anterior.")
    main += [
        '[[ ! -e "${archive}" ]] || die "Ya existe ${archive}; no se sobrescribe"',
        "",
        "local -a exclude_args=()",
        "local pattern",
        'for pattern in "${EXCLUDES[@]}"; do',
        '    exclude_args+=("--exclude=${pattern}")',
        "done",
        "",
        'info "Creando ${archive}"',
    ]
    main += ctx.explain("-C cambia al directorio padre para guardar rutas relativas en el archivo.")
    main += [
        f'tar -c{flag}f "${{archive}}" "${{exclude_args[@]}}" -C "$(dirname -- "${{SOURCE_DIR}}")" "$(basename -- "${{SOURCE_DIR}}")"',
        'ok "Copia creada: ${archive} ($(du -h -- "${archive}" | cut -f1))"',
    ]
    return TemplateBody(globals=globals_, main=main, uses_logging=True)


def _backup_cross_check(values: Mapping[str, Any]) -> dict[str, str]:
    source, dest = path_key(values["source_dir"]), path_key(values["dest_dir"])
    if source == dest:
        return {"dest_dir": "El destino no puede ser el mismo directorio que el origen"}
    if dest.startswith(source.rstrip("/") + "/"):
        return {"dest_dir": "El destino no puede estar dentro del origen (la copia se incluiría a sí misma)"}
    return {}


BACKUP = Template(
    id="backup",
    name="Copia de seguridad",
    description="Archiva un directorio en un .tar comprimido con fecha y hora, con exclusiones.",
    category="Archivos",
    script_description="Crea una copia comprimida y fechada de un directorio.",
    params=(
        TemplateParam("source_dir", "Directorio de origen", ParamType.PATH, "~/Documentos", check=_check_path),
        TemplateParam("dest_dir", "Directorio de destino", ParamType.PATH, "~/Copias",
                      "Se crea si no existe.", check=_check_path),
        TemplateParam("compression", "Compresión", ParamType.CHOICE, "gzip",
                      choices=(("gzip", "gzip (.tar.gz)"), ("xz", "xz (.tar.xz, más lenta)"), ("none", "Sin comprimir (.tar)"))),
        TemplateParam("excludes", "Excluir", ParamType.LIST, [".cache", "node_modules"],
                      "Patrones de tar --exclude, separados por comas.", required=False),
    ),
    risk=Risk(RiskLevel.MEDIUM, (
        "Crea un archivo nuevo en el destino; no borra ni modifica el origen.",
        "Comprueba que el destino tiene espacio libre suficiente.",
        "Las copias antiguas no se eliminan automáticamente.",
    )),
    build=_build_backup,
    cross_check=_backup_cross_check,
    tags=("tar", "backup", "comprimir"),
)

# ---------------------------------------------------------------------------
# 4. Limpieza controlada de archivos temporales
# ---------------------------------------------------------------------------

def _build_cleanup(ctx: BuildContext) -> TemplateBody:
    depth = "" if ctx.values["recursive"] else " -maxdepth 1"
    globals_ = [
        f"readonly TARGET_DIR={path_literal(ctx.values['target_dir'])}",
        f"readonly NAME_PATTERN={single_quote(ctx.values['pattern'])}",
        f"readonly MIN_AGE_DAYS={ctx.values['min_age_days']}",
        "",
        "APPLY=false",
        "ASSUME_YES=false",
    ]
    usage = "\n".join([
        "usage() {",
        "    cat <<EOF",
        'Uso: ${0##*/} [--apply] [--yes]',
        "",
        "Busca archivos «${NAME_PATTERN}» con más de ${MIN_AGE_DAYS} días en ${TARGET_DIR}.",
        "Sin --apply solo los muestra (simulación).",
        "",
        "  --apply    Borra los archivos encontrados",
        "  -y, --yes  No pide confirmación antes de borrar",
        "  -h, --help Muestra esta ayuda",
        "EOF",
        "}",
    ])
    parse = _parse_args_function([("--apply", "APPLY", "true"), ("-y|--yes", "ASSUME_YES", "true")])
    check = "\n".join([
        "resolve_target() {",
        "    local resolved home",
        '    [[ -d "${TARGET_DIR}" ]] || die "No existe el directorio: ${TARGET_DIR}"',
        '    resolved="$(cd -- "${TARGET_DIR}" && pwd -P)"',
        '    home="$(cd -- "${HOME}" && pwd -P)"',
        '    case "${resolved}" in',
        "        /|/bin|/boot|/dev|/etc|/home|/lib|/lib64|/opt|/proc|/root|/sbin|/sys|/usr|/var)",
        '            die "Directorio protegido; no se limpia: ${resolved}" ;;',
        "    esac",
        '    [[ "${resolved}" != "${home}" ]] || die "No se limpia la carpeta personal completa"',
        "    printf '%s\\n' \"${resolved}\"",
        "}",
    ])
    main = ['parse_args "$@"', "", "local dir"]
    main += ctx.explain("|| exit garantiza que un error en la subshell detiene el script también sin modo estricto.")
    main += [
        'dir="$(resolve_target)" || exit 1',
        "",
        "local -a files=()",
        f'mapfile -d \'\' -t files < <(find "${{dir}}"{depth} -type f -name "${{NAME_PATTERN}}" -mmin +"$((MIN_AGE_DAYS * 1440))" -print0)',
        "",
        "if [[ ${#files[@]} -eq 0 ]]; then",
        '    ok "No hay archivos que limpiar en ${dir}"',
        "    return 0",
        "fi",
        "",
        'info "Archivos encontrados (${#files[@]}):"',
        "printf '  %s\\n' \"${files[@]}\" >&2",
        "",
        'if [[ "${APPLY}" != true ]]; then',
        '    warn "Simulación: no se ha borrado nada. Ejecuta con --apply para borrar."',
        "    return 0",
        "fi",
        "",
        'if [[ "${ASSUME_YES}" != true ]]; then',
        '    local answer=""',
        '    read -r -p "¿Borrar ${#files[@]} archivo(s) de forma permanente? [s/N] " answer || answer=""',
        '    if [[ ! "${answer}" =~ ^[sS]$ ]]; then',
        '        info "Operación cancelada"',
        "        return 0",
        "    fi",
        "fi",
        "",
        'rm -f -- "${files[@]}"',
        'ok "Borrados ${#files[@]} archivo(s)"',
    ]
    return TemplateBody(globals=globals_, functions=[usage, parse, check], main=main, uses_logging=True)


def _check_pattern(value: str) -> str | None:
    if "/" in value:
        return "El patrón no puede contener /"
    if value.strip("*?") == "":
        return "El patrón es demasiado amplio; incluye parte del nombre, p. ej. *.tmp"
    return None


CLEANUP = Template(
    id="cleanup-temp",
    name="Limpieza de temporales",
    description="Lista y, solo con --apply y confirmación, borra archivos antiguos que coinciden con un patrón.",
    category="Archivos",
    script_description="Borra archivos temporales antiguos de un directorio, con simulación previa.",
    usage="[--apply] [--yes]",
    params=(
        TemplateParam("target_dir", "Directorio a limpiar", ParamType.PATH, "~/.cache/mi-aplicacion/tmp",
                      "No se admiten carpetas del sistema ni la carpeta personal completa.",
                      check=_check_modifiable_path),
        TemplateParam("pattern", "Patrón de nombre", ParamType.TEXT, "*.tmp", "Patrón de find -name.",
                      check=_check_pattern),
        TemplateParam("min_age_days", "Antigüedad mínima (días)", ParamType.INT, 7, minimum=0, maximum=3650),
        TemplateParam("recursive", "Incluir subdirectorios", ParamType.BOOL, False),
    ),
    risk=Risk(RiskLevel.HIGH, (
        "Con --apply borra archivos de forma permanente (no van a la papelera).",
        "Por defecto el script solo muestra lo que borraría.",
        "Pide confirmación salvo que se use --yes.",
        "Rechaza carpetas del sistema y la carpeta personal completa.",
    )),
    build=_build_cleanup,
    tags=("rm", "find", "temporales", "limpiar"),
)

# ---------------------------------------------------------------------------
# 5. Monitorización de procesos
# ---------------------------------------------------------------------------

def _build_monitor(ctx: BuildContext) -> TemplateBody:
    globals_ = [
        f"readonly PROCESS_NAME={single_quote(ctx.values['process_name'])}",
        f"readonly INTERVAL_SECONDS={ctx.values['interval']}",
    ]
    globals_ += ctx.explain("0 = comprobar indefinidamente (detén el script con Ctrl+C).")
    globals_.append(f"readonly MAX_CHECKS={ctx.values['max_checks']}")

    check = ["check_once() {", "    local pids"]
    check += ["    " + line for line in ctx.explain(
        "pgrep devuelve 1 cuando no hay coincidencias: se evalúa en un if para",
        "que no se trate como un error del script.")]
    check += [
        '    if pids="$(pgrep -x -- "${PROCESS_NAME}")"; then',
        '        ok "${PROCESS_NAME} en ejecución (PID: ${pids//$\'\\n\'/, })"',
        '        ps -o pid=,pcpu=,pmem=,etime= -p "${pids//$\'\\n\'/,}" | while read -r pid cpu mem elapsed; do',
        '            info "  PID ${pid}: CPU ${cpu}% · memoria ${mem}% · activo ${elapsed}"',
        "        done",
        "        return 0",
        "    fi",
        '    warn "${PROCESS_NAME} no está en ejecución"',
        "    return 1",
        "}",
    ]
    main = [
        'command -v pgrep >/dev/null 2>&1 || die "No se encontró pgrep (paquete procps)"',
        "",
        "local count=0 missing=0",
        "while true; do",
        "    count=$((count + 1))",
        "    check_once || missing=$((missing + 1))",
        '    if [[ "${MAX_CHECKS}" -gt 0 && "${count}" -ge "${MAX_CHECKS}" ]]; then',
        "        break",
        "    fi",
        '    sleep "${INTERVAL_SECONDS}"',
        "done",
        "",
        'info "Comprobaciones: ${count}; sin el proceso: ${missing}"',
    ]
    main += ctx.explain("El código de salida es 1 si el proceso faltó en alguna comprobación.")
    main.append('[[ "${missing}" -eq 0 ]]')
    return TemplateBody(globals=globals_, functions=["\n".join(check)], main=main, uses_logging=True)


MONITOR = Template(
    id="process-monitor",
    name="Monitorización de procesos",
    description="Comprueba periódicamente si un proceso está activo y muestra su uso de CPU y memoria.",
    category="Sistema",
    script_description="Vigila si un proceso está en ejecución y muestra su consumo.",
    params=(
        TemplateParam("process_name", "Nombre del proceso", ParamType.TEXT, "nginx",
                      "Nombre exacto (pgrep -x), máximo 15 caracteres.",
                      check=_regex_check(r"[A-Za-z0-9._+:-]{1,15}", "Nombre de proceso no válido")),
        TemplateParam("interval", "Intervalo (segundos)", ParamType.INT, 5, minimum=1, maximum=86400),
        TemplateParam("max_checks", "Número de comprobaciones", ParamType.INT, 1,
                      "0 = sin límite.", minimum=0, maximum=1_000_000),
    ),
    risk=Risk(RiskLevel.LOW, ("Solo lee información de procesos; no los modifica.",)),
    build=_build_monitor,
    tags=("pgrep", "ps", "procesos", "monitor"),
)

# ---------------------------------------------------------------------------
# 6. Organización de archivos
# ---------------------------------------------------------------------------

def _build_organizer(ctx: BuildContext) -> TemplateBody:
    others = "Otros" if ctx.values["include_others"] else ""
    globals_ = [f"readonly SOURCE_DIR={path_literal(ctx.values['source_dir'])}", "", "APPLY=false"]
    usage = "\n".join([
        "usage() {",
        "    cat <<EOF",
        "Uso: ${0##*/} [--apply]",
        "",
        "Clasifica por extensión los archivos de ${SOURCE_DIR} en subcarpetas.",
        "Sin --apply solo muestra lo que haría (simulación).",
        "EOF",
        "}",
    ])
    parse = _parse_args_function([("--apply", "APPLY", "true")])
    category = "\n".join([
        "category_for() {",
        '    case "${1,,}" in',
        "        jpg|jpeg|png|gif|webp|svg|bmp|heic) printf 'Imagenes' ;;",
        "        pdf|doc|docx|odt|txt|md|rtf|xls|xlsx|ods|ppt|pptx|odp|csv) printf 'Documentos' ;;",
        "        mp3|flac|ogg|wav|m4a|opus) printf 'Audio' ;;",
        "        mp4|mkv|webm|avi|mov) printf 'Video' ;;",
        "        zip|tar|gz|xz|bz2|zst|7z|rar) printf 'Comprimidos' ;;",
        f"        *) printf '%s' {single_quote(others)} ;;",
        "    esac",
        "}",
    ])
    main = [
        'parse_args "$@"',
        '[[ -d "${SOURCE_DIR}" ]] || die "No existe el directorio: ${SOURCE_DIR}"',
        "",
        "local file name category moved=0 skipped=0",
        "shopt -s nullglob",
        'for file in "${SOURCE_DIR}"/*; do',
    ]
    main += ["    " + line for line in ctx.explain("Se ignoran carpetas, enlaces, ocultos y archivos sin extensión.")]
    main += [
        '    [[ -f "${file}" && ! -L "${file}" ]] || continue',
        '    name="${file##*/}"',
        '    if [[ "${name}" != *.* || "${name}" == .* ]]; then',
        "        skipped=$((skipped + 1))",
        "        continue",
        "    fi",
        '    category="$(category_for "${name##*.}")"',
        '    if [[ -z "${category}" ]]; then',
        "        skipped=$((skipped + 1))",
        "        continue",
        "    fi",
        '    if [[ -e "${SOURCE_DIR}/${category}/${name}" ]]; then',
        '        warn "Ya existe ${category}/${name}; se omite"',
        "        skipped=$((skipped + 1))",
        "        continue",
        "    fi",
        '    if [[ "${APPLY}" == true ]]; then',
        '        mkdir -p -- "${SOURCE_DIR}/${category}"',
    ]
    main += ["        " + line for line in ctx.explain("mv -n nunca sobrescribe un archivo existente.")]
    main += [
        '        mv -n -- "${file}" "${SOURCE_DIR}/${category}/"',
        '        info "${name} → ${category}/"',
        "    else",
        '        info "[simulación] ${name} → ${category}/"',
        "    fi",
        "    moved=$((moved + 1))",
        "done",
        "",
        'ok "Archivos clasificados: ${moved}; omitidos: ${skipped}"',
        'if [[ "${APPLY}" != true ]]; then',
        '    warn "Simulación: no se ha movido nada. Ejecuta con --apply para aplicar."',
        "fi",
    ]
    return TemplateBody(globals=globals_, functions=[usage, parse, category], main=main, uses_logging=True)


ORGANIZER = Template(
    id="file-organizer",
    name="Organizador de archivos",
    description="Clasifica los archivos de una carpeta en subcarpetas según su extensión.",
    category="Archivos",
    script_description="Ordena los archivos de una carpeta en subcarpetas por tipo.",
    usage="[--apply]",
    params=(
        TemplateParam("source_dir", "Carpeta a organizar", ParamType.PATH, "~/Descargas",
                      check=_check_modifiable_path),
        TemplateParam("include_others", "Mover extensiones desconocidas a «Otros»", ParamType.BOOL, False),
    ),
    risk=Risk(RiskLevel.MEDIUM, (
        "Con --apply mueve archivos a subcarpetas; nunca sobrescribe (mv -n).",
        "Por defecto el script solo muestra lo que haría.",
        "Otros programas pueden dejar de encontrar los archivos movidos.",
    )),
    build=_build_organizer,
    tags=("mv", "ordenar", "descargas", "extensiones"),
)

# ---------------------------------------------------------------------------
# 7. Comprobación del estado de servicios
# ---------------------------------------------------------------------------

def _build_services(ctx: BuildContext) -> TemplateBody:
    scope = "--user" if ctx.values["user_scope"] else "--system"
    globals_ = [f"readonly SERVICES={array_literal(ctx.values['services'])}", f"readonly SCOPE={scope}"]
    main = [
        'command -v systemctl >/dev/null 2>&1 || die "systemctl no está disponible (¿el sistema usa systemd?)"',
        "",
        "local service state failed=0",
        'for service in "${SERVICES[@]}"; do',
    ]
    main += ["    " + line for line in ctx.explain(
        "is-active devuelve un código distinto de 0 si el servicio no está activo;",
        "se guarda el estado con || true para evaluarlo después.")]
    main += [
        '    state="$(systemctl "${SCOPE}" is-active -- "${service}" 2>/dev/null)" || true',
        '    if [[ "${state}" == active ]]; then',
        '        ok "${service}: activo"',
        "    else",
        '        warn "${service}: ${state:-desconocido}"',
        "        failed=$((failed + 1))",
        "    fi",
        "done",
        "",
        'if [[ "${failed}" -gt 0 ]]; then',
        '    warn "${failed} servicio(s) no están activos"',
        "    return 1",
        "fi",
        'ok "Todos los servicios están activos"',
    ]
    return TemplateBody(globals=globals_, main=main, uses_logging=True)


SERVICES = Template(
    id="service-check",
    name="Estado de servicios",
    description="Comprueba con systemctl si una lista de servicios está activa; sale con 1 si alguno falla.",
    category="Sistema",
    script_description="Comprueba si los servicios indicados están activos.",
    params=(
        TemplateParam("services", "Servicios", ParamType.LIST, ["ssh", "cron"],
                      "Nombres de unidad separados por comas.",
                      check=_regex_check(r"[A-Za-z0-9@._:-]+", "Nombre de servicio no válido")),
        TemplateParam("user_scope", "Servicios de usuario (--user)", ParamType.BOOL, False),
    ),
    risk=Risk(RiskLevel.LOW, ("Solo consulta el estado; no inicia ni detiene servicios.",)),
    build=_build_services,
    tags=("systemctl", "systemd", "servicios"),
)

BUILTIN_TEMPLATES: tuple[Template, ...] = (MINIMAL, CLI_TOOL, BACKUP, CLEANUP, MONITOR, ORGANIZER, SERVICES)

# ---------------------------------------------------------------------------
# Registro con plantillas personalizadas
# ---------------------------------------------------------------------------

_CUSTOM_ID_RE = re.compile(r"^custom-[a-z0-9-]+$")


def slugify(text: str) -> str:
    import unicodedata

    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug or "plantilla"


class TemplateRegistry:
    """Plantillas integradas más las personalizadas guardadas como JSON."""

    def __init__(self, custom_dir: Path | None = None) -> None:
        self.custom_dir = custom_dir
        self.warnings: list[str] = []
        self._custom: dict[str, Template] = {}
        if custom_dir is not None:
            self._load_custom()

    def all(self) -> list[Template]:
        return [*BUILTIN_TEMPLATES, *sorted(self._custom.values(), key=lambda t: t.name.lower())]

    def get(self, template_id: str) -> Template | None:
        for template in BUILTIN_TEMPLATES:
            if template.id == template_id:
                return template
        return self._custom.get(template_id)

    def categories(self) -> list[str]:
        seen: list[str] = []
        for template in self.all():
            if template.category not in seen:
                seen.append(template.category)
        return seen

    def search(self, query: str = "", category: str | None = None, only: set[str] | None = None) -> list[Template]:
        needle = query.strip().lower()
        result = []
        for template in self.all():
            if category and template.category != category:
                continue
            if only is not None and template.id not in only:
                continue
            haystack = " ".join([template.name, template.description, template.category, *template.tags]).lower()
            if needle and needle not in haystack:
                continue
            result.append(template)
        return result

    # -- personalizadas ----------------------------------------------------

    def _load_custom(self) -> None:
        assert self.custom_dir is not None
        if not self.custom_dir.is_dir():
            return
        for path in sorted(self.custom_dir.glob("*.json")):
            result = read_json(path, default=None)
            if result.error:
                self.warnings.append(result.error)
            data = result.data
            if not isinstance(data, dict):
                continue
            try:
                template = self._from_dict(data)
            except (KeyError, TypeError, ValueError) as exc:
                self.warnings.append(f"Plantilla {path.name} ignorada: {exc}")
                continue
            self._custom[template.id] = template

    @staticmethod
    def _from_dict(data: dict[str, Any]) -> Template:
        template_id = str(data["id"])
        if not _CUSTOM_ID_RE.match(template_id):
            raise ValueError("identificador no válido")
        body = data["body"]
        if not isinstance(body, str) or not body.strip():
            raise ValueError("el código está vacío")
        return Template(
            id=template_id,
            name=str(data["name"]),
            description=str(data.get("description", "")),
            category=CUSTOM_CATEGORY,
            script_description=str(data.get("description", "")) or "Script creado a partir de una plantilla propia.",
            custom_body=body,
            tags=("personalizada",),
        )

    def save_custom(self, name: str, description: str, body: str) -> Template:
        if self.custom_dir is None:
            raise RuntimeError("No hay carpeta para plantillas personalizadas")
        name = " ".join(name.split())
        if not name:
            raise ValueError("La plantilla necesita un nombre")
        if not body.strip():
            raise ValueError("El código de la plantilla está vacío")
        base = f"custom-{slugify(name)}"
        template_id, counter = base, 2
        while self.get(template_id) is not None:
            template_id = f"{base}-{counter}"
            counter += 1
        data = {"id": template_id, "name": name, "description": " ".join(description.split()), "body": body}
        write_json(self.custom_dir / f"{template_id}.json", data)
        template = self._from_dict(data)
        self._custom[template_id] = template
        return template

    def delete_custom(self, template_id: str) -> None:
        if template_id not in self._custom or self.custom_dir is None:
            raise KeyError(template_id)
        (self.custom_dir / f"{template_id}.json").unlink(missing_ok=True)
        del self._custom[template_id]
