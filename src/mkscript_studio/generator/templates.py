"""Built-in templates and the template registry (built-in + custom).

Templates never run anything when generated: they only produce text. Those
that can modify data run in dry-run mode unless the generated script is
explicitly given ``--apply``.
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

CUSTOM_CATEGORY = "Custom"

# ---------------------------------------------------------------------------
# Reusable checks
# ---------------------------------------------------------------------------

_PROTECTED_PATHS = {"/", "~", "/bin", "/boot", "/dev", "/etc", "/home", "/lib", "/lib64",
                    "/opt", "/proc", "/root", "/sbin", "/sys", "/usr", "/var"}


def _check_path(value: str) -> str | None:
    if not is_absolute_or_home(value):
        return "Use an absolute path or one starting with ~/"
    return None


def _check_modifiable_path(value: str) -> str | None:
    error = _check_path(value)
    if error:
        return error
    if path_key(value) in _PROTECTED_PATHS:
        return "For safety, system folders and the whole home folder are not allowed"
    return None


def _regex_check(pattern: str, message: str):
    compiled = re.compile(pattern)

    def check(value: Any) -> str | None:
        items = value if isinstance(value, list) else [value]
        bad = [item for item in items if not compiled.fullmatch(item)]
        return f"{message}: {', '.join(bad)}" if bad else None

    return check


def _parse_args_function(options: list[tuple[str, str, str]], extra_cases: Iterable[str] = ()) -> str:
    """Argument parsing loop with -h/--help and the given options.

    ``options`` holds ``(case pattern, variable, value)`` tuples.
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
        '            *) usage >&2; die "Unknown argument: $1" ;;',
        "        esac",
        "        shift",
        "    done",
        "}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 1. Minimal script
# ---------------------------------------------------------------------------

def _build_minimal(ctx: BuildContext) -> TemplateBody:
    main = ctx.explain("Write the main logic here. The arguments are in \"$@\".")
    main.append(f"echo {single_quote(ctx.values['message'])}")
    return TemplateBody(main=main)


MINIMAL = Template(
    id="minimal",
    name="Minimal script",
    description="A clean starting point: header, optional strict mode and a main function.",
    category="Basics",
    script_description="Prints a welcome message.",
    usage="[arguments]",
    params=(
        TemplateParam("message", "Message", ParamType.TEXT, "Hello from MKSCRIPT Studio",
                      "Text the script prints with echo."),
    ),
    risk=Risk(RiskLevel.LOW, ("Only prints a message; review the risks of any logic you add.",)),
    build=_build_minimal,
    tags=("start", "main", "echo"),
)

# ---------------------------------------------------------------------------
# 2. CLI tool with arguments
# ---------------------------------------------------------------------------

def _flag_var(flag: str) -> str:
    # The prefix avoids clobbering variables such as PATH or HOME.
    return "OPT_" + flag.upper().replace("-", "_")


def _build_cli(ctx: BuildContext) -> TemplateBody:
    flags: list[str] = ctx.values["flags"]
    positional: str = ctx.values["positional"]
    required: bool = ctx.values["positional_required"]
    pos_var = "ARG_" + positional.upper()
    pos_usage = f"<{positional}>" if required else f"[{positional}]"

    globals_ = ['readonly SCRIPT_NAME="${0##*/}"', f"readonly VERSION={single_quote(ctx.values['version'])}", ""]
    globals_ += ctx.explain("Option state; parse_args updates it.")
    globals_ += [f"{_flag_var(flag)}=false" for flag in flags]
    globals_.append(f'{pos_var}=""')

    width = max([len(f"--{flag}") for flag in flags] + [len("--version")])
    help_lines = [
        "usage() {",
        "    cat <<EOF",
        f"Usage: ${{SCRIPT_NAME}} [options] {pos_usage}",
        "",
        "Options:",
        f"  -h, {'--help'.ljust(width)}  Show this help and exit",
        f"  -V, {'--version'.ljust(width)}  Show the version and exit",
    ]
    help_lines += [f"      {f'--{flag}'.ljust(width)}  Enable the '{flag}' option" for flag in flags]
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
        '            -*) usage >&2; die "Unknown option: $1" ;;',
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
            f'        die "Missing argument {pos_usage}"',
            "    fi",
        ]
    parse += [
        f'    if [[ $# -gt 0 ]]; then {pos_var}="$1"; shift; fi',
        '    [[ $# -eq 0 ]] || die "Too many arguments: $*"',
        "}",
    ]

    main = ['parse_args "$@"', ""]
    main += ctx.explain("Replace these lines with the tool's logic.")
    main.append(f'info "{positional}: ${{{pos_var}:-(not given)}}"')
    main += [f'info "--{flag}: ${{{_flag_var(flag)}}}"' for flag in flags]

    return TemplateBody(
        globals=globals_,
        functions=["\n".join(help_lines), "\n".join(parse)],
        main=main,
        uses_logging=True,
        usage=f"[options] {pos_usage}",
    )


def _check_flags(values: list[str]) -> str | None:
    error = _regex_check(r"[a-z][a-z0-9-]*", "Invalid option name (lowercase letters, digits and hyphens)")(values)
    if error:
        return error
    reserved = {"help", "version"} & set(values)
    if reserved:
        return f"Reserved option: {', '.join(sorted(reserved))}"
    if len(set(values)) != len(values):
        return "There are duplicate options"
    return None


CLI_TOOL = Template(
    id="cli-tool",
    name="CLI tool with arguments",
    description="Option parsing with --help and --version, boolean flags and a positional argument.",
    category="Basics",
    script_description="Command-line tool with options and built-in help.",
    usage="[options] <argument>",
    params=(
        TemplateParam("version", "Version", ParamType.TEXT, "0.1.0", "Shown with --version.",
                      check=_regex_check(r"[0-9A-Za-z.+-]+", "Invalid version")),
        TemplateParam("flags", "Boolean flags", ParamType.LIST, ["verbose", "dry-run"],
                      "Comma-separated; each one creates --flag.", required=False, check=_check_flags),
        TemplateParam("positional", "Positional argument", ParamType.TEXT, "file",
                      "Argument name (lowercase).",
                      check=_regex_check(r"[a-z][a-z0-9_]*", "Use lowercase letters, digits or _")),
        TemplateParam("positional_required", "Argument is required", ParamType.BOOL, True),
    ),
    risk=Risk(RiskLevel.LOW, (
        "The generated script only parses arguments and prints the values it received.",
        "Review the risks of any logic you add.",
    )),
    build=_build_cli,
    tags=("arguments", "options", "flags", "getopts", "help"),
)

# ---------------------------------------------------------------------------
# 3. Backup
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
        'command -v tar >/dev/null 2>&1 || die "tar was not found"',
        '[[ -d "${SOURCE_DIR}" ]] || die "Source directory does not exist: ${SOURCE_DIR}"',
        '[[ -r "${SOURCE_DIR}" ]] || die "Cannot read the source directory: ${SOURCE_DIR}"',
        'mkdir -p -- "${DEST_DIR}"',
        "",
        "local stamp archive",
        'stamp="$(date +%Y%m%d-%H%M%S)"',
        f'archive="${{DEST_DIR}}/$(basename -- "${{SOURCE_DIR}}")-${{stamp}}{extension}"',
    ]
    main += ctx.explain("A previous backup is never overwritten.")
    main += [
        '[[ ! -e "${archive}" ]] || die "${archive} already exists; not overwriting it"',
        "",
        "local -a exclude_args=()",
        "local pattern",
        'for pattern in "${EXCLUDES[@]}"; do',
        '    exclude_args+=("--exclude=${pattern}")',
        "done",
        "",
        'info "Creating ${archive}"',
    ]
    main += ctx.explain("-C switches to the parent directory so the archive stores relative paths.")
    main += [
        f'tar -c{flag}f "${{archive}}" "${{exclude_args[@]}}" -C "$(dirname -- "${{SOURCE_DIR}}")" "$(basename -- "${{SOURCE_DIR}}")"',
        'ok "Backup created: ${archive} ($(du -h -- "${archive}" | cut -f1))"',
    ]
    return TemplateBody(globals=globals_, main=main, uses_logging=True)


def _backup_cross_check(values: Mapping[str, Any]) -> dict[str, str]:
    source, dest = path_key(values["source_dir"]), path_key(values["dest_dir"])
    if source == dest:
        return {"dest_dir": "The destination cannot be the same directory as the source"}
    if dest.startswith(source.rstrip("/") + "/"):
        return {"dest_dir": "The destination cannot be inside the source (the backup would include itself)"}
    return {}


BACKUP = Template(
    id="backup",
    name="Backup",
    description="Archives a directory into a timestamped, compressed .tar file, with exclusions.",
    category="Files",
    script_description="Creates a compressed, timestamped backup of a directory.",
    params=(
        TemplateParam("source_dir", "Source directory", ParamType.PATH, "~/Documents", check=_check_path),
        TemplateParam("dest_dir", "Destination directory", ParamType.PATH, "~/Backups",
                      "Created if it does not exist.", check=_check_path),
        TemplateParam("compression", "Compression", ParamType.CHOICE, "gzip",
                      choices=(("gzip", "gzip (.tar.gz)"), ("xz", "xz (.tar.xz, slower)"), ("none", "Uncompressed (.tar)"))),
        TemplateParam("excludes", "Exclude", ParamType.LIST, [".cache", "node_modules"],
                      "tar --exclude patterns, comma-separated.", required=False),
    ),
    risk=Risk(RiskLevel.MEDIUM, (
        "Creates a new file in the destination; it never deletes or changes the source.",
        "Make sure the destination has enough free space.",
        "Old backups are not removed automatically.",
    )),
    build=_build_backup,
    cross_check=_backup_cross_check,
    tags=("tar", "backup", "archive", "compress"),
)

# ---------------------------------------------------------------------------
# 4. Controlled cleanup of temporary files
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
        'Usage: ${0##*/} [--apply] [--yes]',
        "",
        "Finds files matching '${NAME_PATTERN}' older than ${MIN_AGE_DAYS} days in ${TARGET_DIR}.",
        "Without --apply it only lists them (dry run).",
        "",
        "  --apply    Delete the files found",
        "  -y, --yes  Do not ask for confirmation before deleting",
        "  -h, --help Show this help",
        "EOF",
        "}",
    ])
    parse = _parse_args_function([("--apply", "APPLY", "true"), ("-y|--yes", "ASSUME_YES", "true")])
    check = "\n".join([
        "resolve_target() {",
        "    local resolved home",
        '    [[ -d "${TARGET_DIR}" ]] || die "Directory does not exist: ${TARGET_DIR}"',
        '    resolved="$(cd -- "${TARGET_DIR}" && pwd -P)"',
        '    home="$(cd -- "${HOME}" && pwd -P)"',
        '    case "${resolved}" in',
        "        /|/bin|/boot|/dev|/etc|/home|/lib|/lib64|/opt|/proc|/root|/sbin|/sys|/usr|/var)",
        '            die "Protected directory; not cleaning: ${resolved}" ;;',
        "    esac",
        '    [[ "${resolved}" != "${home}" ]] || die "Refusing to clean the whole home folder"',
        "    printf '%s\\n' \"${resolved}\"",
        "}",
    ])
    main = ['parse_args "$@"', "", "local dir"]
    main += ctx.explain("|| exit makes an error in the subshell stop the script even without strict mode.")
    main += [
        'dir="$(resolve_target)" || exit 1',
        "",
        "local -a files=()",
        f'mapfile -d \'\' -t files < <(find "${{dir}}"{depth} -type f -name "${{NAME_PATTERN}}" -mmin +"$((MIN_AGE_DAYS * 1440))" -print0)',
        "",
        "if [[ ${#files[@]} -eq 0 ]]; then",
        '    ok "Nothing to clean in ${dir}"',
        "    return 0",
        "fi",
        "",
        'info "Files found (${#files[@]}):"',
        "printf '  %s\\n' \"${files[@]}\" >&2",
        "",
        'if [[ "${APPLY}" != true ]]; then',
        '    warn "Dry run: nothing was deleted. Run with --apply to delete."',
        "    return 0",
        "fi",
        "",
        'if [[ "${ASSUME_YES}" != true ]]; then',
        '    local answer=""',
        '    read -r -p "Permanently delete ${#files[@]} file(s)? [y/N] " answer || answer=""',
        '    if [[ ! "${answer}" =~ ^[yY]$ ]]; then',
        '        info "Cancelled"',
        "        return 0",
        "    fi",
        "fi",
        "",
        'rm -f -- "${files[@]}"',
        'ok "Deleted ${#files[@]} file(s)"',
    ]
    return TemplateBody(globals=globals_, functions=[usage, parse, check], main=main, uses_logging=True)


def _check_pattern(value: str) -> str | None:
    if "/" in value:
        return "The pattern cannot contain /"
    if value.strip("*?") == "":
        return "The pattern is too broad; include part of the name, e.g. *.tmp"
    return None


CLEANUP = Template(
    id="cleanup-temp",
    name="Temporary files cleanup",
    description="Lists old files matching a pattern and deletes them only with --apply and confirmation.",
    category="Files",
    script_description="Deletes old temporary files from a directory, with a dry run first.",
    usage="[--apply] [--yes]",
    params=(
        TemplateParam("target_dir", "Directory to clean", ParamType.PATH, "~/.cache/my-app/tmp",
                      "System folders and the whole home folder are not allowed.",
                      check=_check_modifiable_path),
        TemplateParam("pattern", "Name pattern", ParamType.TEXT, "*.tmp", "find -name pattern.",
                      check=_check_pattern),
        TemplateParam("min_age_days", "Minimum age (days)", ParamType.INT, 7, minimum=0, maximum=3650),
        TemplateParam("recursive", "Include subdirectories", ParamType.BOOL, False),
    ),
    risk=Risk(RiskLevel.HIGH, (
        "With --apply it permanently deletes files (they do not go to the trash).",
        "By default the script only shows what it would delete.",
        "It asks for confirmation unless --yes is used.",
        "It refuses system folders and the whole home folder.",
    )),
    build=_build_cleanup,
    tags=("rm", "find", "temporary", "clean"),
)

# ---------------------------------------------------------------------------
# 5. Process monitoring
# ---------------------------------------------------------------------------

def _build_monitor(ctx: BuildContext) -> TemplateBody:
    globals_ = [
        f"readonly PROCESS_NAME={single_quote(ctx.values['process_name'])}",
        f"readonly INTERVAL_SECONDS={ctx.values['interval']}",
    ]
    globals_ += ctx.explain("0 = check forever (stop the script with Ctrl+C).")
    globals_.append(f"readonly MAX_CHECKS={ctx.values['max_checks']}")

    check = ["check_once() {", "    local pids"]
    check += ["    " + line for line in ctx.explain(
        "pgrep returns 1 when nothing matches: it is evaluated inside an if so",
        "that it is not treated as a script error.")]
    check += [
        '    if pids="$(pgrep -x -- "${PROCESS_NAME}")"; then',
        '        ok "${PROCESS_NAME} is running (PID: ${pids//$\'\\n\'/, })"',
        '        ps -o pid=,pcpu=,pmem=,etime= -p "${pids//$\'\\n\'/,}" | while read -r pid cpu mem elapsed; do',
        '            info "  PID ${pid}: CPU ${cpu}% · memory ${mem}% · uptime ${elapsed}"',
        "        done",
        "        return 0",
        "    fi",
        '    warn "${PROCESS_NAME} is not running"',
        "    return 1",
        "}",
    ]
    main = [
        'command -v pgrep >/dev/null 2>&1 || die "pgrep was not found (procps package)"',
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
        'info "Checks: ${count}; process missing: ${missing}"',
    ]
    main += ctx.explain("The exit code is 1 if the process was missing in any check.")
    main.append('[[ "${missing}" -eq 0 ]]')
    return TemplateBody(globals=globals_, functions=["\n".join(check)], main=main, uses_logging=True)


MONITOR = Template(
    id="process-monitor",
    name="Process monitor",
    description="Periodically checks whether a process is running and shows its CPU and memory usage.",
    category="System",
    script_description="Watches whether a process is running and shows its resource usage.",
    params=(
        TemplateParam("process_name", "Process name", ParamType.TEXT, "nginx",
                      "Exact name (pgrep -x), up to 15 characters.",
                      check=_regex_check(r"[A-Za-z0-9._+:-]{1,15}", "Invalid process name")),
        TemplateParam("interval", "Interval (seconds)", ParamType.INT, 5, minimum=1, maximum=86400),
        TemplateParam("max_checks", "Number of checks", ParamType.INT, 1,
                      "0 = no limit.", minimum=0, maximum=1_000_000),
    ),
    risk=Risk(RiskLevel.LOW, ("Only reads process information; it does not change processes.",)),
    build=_build_monitor,
    tags=("pgrep", "ps", "processes", "monitor"),
)

# ---------------------------------------------------------------------------
# 6. File organizer
# ---------------------------------------------------------------------------

def _build_organizer(ctx: BuildContext) -> TemplateBody:
    others = "Other" if ctx.values["include_others"] else ""
    globals_ = [f"readonly SOURCE_DIR={path_literal(ctx.values['source_dir'])}", "", "APPLY=false"]
    usage = "\n".join([
        "usage() {",
        "    cat <<EOF",
        "Usage: ${0##*/} [--apply]",
        "",
        "Sorts the files in ${SOURCE_DIR} into subfolders by extension.",
        "Without --apply it only shows what it would do (dry run).",
        "EOF",
        "}",
    ])
    parse = _parse_args_function([("--apply", "APPLY", "true")])
    category = "\n".join([
        "category_for() {",
        '    case "${1,,}" in',
        "        jpg|jpeg|png|gif|webp|svg|bmp|heic) printf 'Images' ;;",
        "        pdf|doc|docx|odt|txt|md|rtf|xls|xlsx|ods|ppt|pptx|odp|csv) printf 'Documents' ;;",
        "        mp3|flac|ogg|wav|m4a|opus) printf 'Audio' ;;",
        "        mp4|mkv|webm|avi|mov) printf 'Video' ;;",
        "        zip|tar|gz|xz|bz2|zst|7z|rar) printf 'Archives' ;;",
        f"        *) printf '%s' {single_quote(others)} ;;",
        "    esac",
        "}",
    ])
    main = [
        'parse_args "$@"',
        '[[ -d "${SOURCE_DIR}" ]] || die "Directory does not exist: ${SOURCE_DIR}"',
        "",
        "local file name category moved=0 skipped=0",
        "shopt -s nullglob",
        'for file in "${SOURCE_DIR}"/*; do',
    ]
    main += ["    " + line for line in ctx.explain("Folders, links, hidden files and files without an extension are skipped.")]
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
        '        warn "${category}/${name} already exists; skipping"',
        "        skipped=$((skipped + 1))",
        "        continue",
        "    fi",
        '    if [[ "${APPLY}" == true ]]; then',
        '        mkdir -p -- "${SOURCE_DIR}/${category}"',
    ]
    main += ["        " + line for line in ctx.explain("mv -n never overwrites an existing file.")]
    main += [
        '        mv -n -- "${file}" "${SOURCE_DIR}/${category}/"',
        '        info "${name} → ${category}/"',
        "    else",
        '        info "[dry run] ${name} → ${category}/"',
        "    fi",
        "    moved=$((moved + 1))",
        "done",
        "",
        'ok "Files sorted: ${moved}; skipped: ${skipped}"',
        'if [[ "${APPLY}" != true ]]; then',
        '    warn "Dry run: nothing was moved. Run with --apply to apply."',
        "fi",
    ]
    return TemplateBody(globals=globals_, functions=[usage, parse, category], main=main, uses_logging=True)


ORGANIZER = Template(
    id="file-organizer",
    name="File organizer",
    description="Sorts the files in a folder into subfolders by extension.",
    category="Files",
    script_description="Sorts the files in a folder into subfolders by type.",
    usage="[--apply]",
    params=(
        TemplateParam("source_dir", "Folder to organize", ParamType.PATH, "~/Downloads",
                      check=_check_modifiable_path),
        TemplateParam("include_others", "Move unknown extensions to 'Other'", ParamType.BOOL, False),
    ),
    risk=Risk(RiskLevel.MEDIUM, (
        "With --apply it moves files into subfolders; it never overwrites (mv -n).",
        "By default the script only shows what it would do.",
        "Other programs may no longer find the moved files.",
    )),
    build=_build_organizer,
    tags=("mv", "sort", "downloads", "extensions"),
)

# ---------------------------------------------------------------------------
# 7. Service status check
# ---------------------------------------------------------------------------

def _build_services(ctx: BuildContext) -> TemplateBody:
    scope = "--user" if ctx.values["user_scope"] else "--system"
    globals_ = [f"readonly SERVICES={array_literal(ctx.values['services'])}", f"readonly SCOPE={scope}"]
    main = [
        'command -v systemctl >/dev/null 2>&1 || die "systemctl is not available (does this system use systemd?)"',
        "",
        "local service state failed=0",
        'for service in "${SERVICES[@]}"; do',
    ]
    main += ["    " + line for line in ctx.explain(
        "is-active returns a non-zero code when the service is not active;",
        "the state is captured with || true and evaluated afterwards.")]
    main += [
        '    state="$(systemctl "${SCOPE}" is-active -- "${service}" 2>/dev/null)" || true',
        '    if [[ "${state}" == active ]]; then',
        '        ok "${service}: active"',
        "    else",
        '        warn "${service}: ${state:-unknown}"',
        "        failed=$((failed + 1))",
        "    fi",
        "done",
        "",
        'if [[ "${failed}" -gt 0 ]]; then',
        '    warn "${failed} service(s) are not active"',
        "    return 1",
        "fi",
        'ok "All services are active"',
    ]
    return TemplateBody(globals=globals_, main=main, uses_logging=True)


SERVICES = Template(
    id="service-check",
    name="Service status",
    description="Uses systemctl to check whether a list of services is active; exits with 1 if any is not.",
    category="System",
    script_description="Checks whether the given services are active.",
    params=(
        TemplateParam("services", "Services", ParamType.LIST, ["ssh", "cron"],
                      "Comma-separated unit names.",
                      check=_regex_check(r"[A-Za-z0-9@._:-]+", "Invalid service name")),
        TemplateParam("user_scope", "User services (--user)", ParamType.BOOL, False),
    ),
    risk=Risk(RiskLevel.LOW, ("Only queries the state; it never starts or stops services.",)),
    build=_build_services,
    tags=("systemctl", "systemd", "services"),
)

BUILTIN_TEMPLATES: tuple[Template, ...] = (MINIMAL, CLI_TOOL, BACKUP, CLEANUP, MONITOR, ORGANIZER, SERVICES)

# ---------------------------------------------------------------------------
# Registry including custom templates
# ---------------------------------------------------------------------------

_CUSTOM_ID_RE = re.compile(r"^custom-[a-z0-9-]+$")


def slugify(text: str) -> str:
    import unicodedata

    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug or "template"


class TemplateRegistry:
    """Built-in templates plus custom ones stored as JSON."""

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

    # -- custom templates ----------------------------------------------------

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
                self.warnings.append(f"Template {path.name} ignored: {exc}")
                continue
            self._custom[template.id] = template

    @staticmethod
    def _from_dict(data: dict[str, Any]) -> Template:
        template_id = str(data["id"])
        if not _CUSTOM_ID_RE.match(template_id):
            raise ValueError("invalid identifier")
        body = data["body"]
        if not isinstance(body, str) or not body.strip():
            raise ValueError("the code is empty")
        return Template(
            id=template_id,
            name=str(data["name"]),
            description=str(data.get("description", "")),
            category=CUSTOM_CATEGORY,
            script_description=str(data.get("description", "")) or "Script created from a custom template.",
            custom_body=body,
            tags=("custom",),
        )

    def save_custom(self, name: str, description: str, body: str) -> Template:
        if self.custom_dir is None:
            raise RuntimeError("There is no folder for custom templates")
        name = " ".join(name.split())
        if not name:
            raise ValueError("The template needs a name")
        if not body.strip():
            raise ValueError("The template code is empty")
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
