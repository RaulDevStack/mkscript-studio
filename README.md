# MKSCRIPT Studio

A visual workshop to **create, customize, edit, validate and save Bash scripts** on Linux.
It is the evolution of [mkscript](https://github.com/RaulDevStack/mkscript): it keeps its ideas
(templates, validated names, automatic `.sh` extension, executable permissions, never overwriting
files, opening the result in an editor) and turns them into a GTK 4 desktop application with a
built-in code editor.

The Bash code is always visible and editable. The forms only help you generate it.

## Features

| Section | What it includes |
| --- | --- |
| **Home** | New script, open file, recent files, favorite templates, shortcuts and a summary of your preferences. |
| **Library** | 7 templates by category, search, category and favorites filters, validated parameter form, risk notice, live preview, "Open in Studio" and "Generate and save". Custom templates (create them from Studio and delete them). |
| **Studio** | GtkSourceView editor with Bash highlighting, line numbers, configurable indentation, undo/redo, find and replace (match case, whole word, regex), Tab completion, a files panel for the current folder, a generation panel with preview, a problems panel (bash -n and ShellCheck, with marks in the gutter) and an execution output panel. Status bar with path, save state, line/column and indentation. |
| **Projects** | Create, add an existing folder, rename, duplicate, remove from the list (without deleting files), open the folder and open its scripts. |
| **Preferences** | Shell, comment style, strict mode, colors, output folder, permissions, backups, editor, autocompletion and theme. Saved immediately. |

### Templates

| Id | Template | Risk |
| --- | --- | --- |
| `minimal` | Minimal script | Low |
| `cli-tool` | CLI tool with arguments (`--help`, `--version`, flags, positional argument) | Low |
| `backup` | Timestamped `tar` backup with exclusions | Medium |
| `cleanup-temp` | Controlled cleanup of temporary files (dry run by default, `--apply` and confirmation) | High |
| `process-monitor` | Monitor a process with `pgrep`/`ps` | Low |
| `file-organizer` | Sort files by extension (dry run by default, `mv -n`) | Medium |
| `service-check` | Service status with `systemctl is-active` | Low |

Templates that can modify data only act with `--apply`, check their paths and refuse system folders
or the whole home folder. Legitimate exceptions to strict mode (`pgrep` with no match, `systemctl
is-active` on a stopped service) are handled explicitly inside `if` or with a commented `|| true`.
Generating a template never runs anything.

### Autocompletion

- `ec` + <kbd>Tab</kbd> → `echo`; `mkd` → `mkdir`; `gre` → `grep`.
- With several matches (`pr` → `printf`, `printenv`), Tab extends the common prefix and shows a
  list. ↑/↓ to move, <kbd>Tab</kbd> or <kbd>Enter</kbd> to accept, <kbd>Esc</kbd> to close.
- With no match, at the start of a line, in comments or inside single quotes, Tab indents as usual.
  With a selection, Tab indents the selection.
- Only the prefix before the cursor is replaced.
- Distinguishes functions defined in the file, builtins, keywords, commands, variables (`$VAR`,
  `${VAR}`) and paths (`./`, `~/`, `/`). Suggests `in` after `for x`. Commands from `PATH` are optional.
- Suggestions appear while typing (configurable) and with <kbd>Ctrl</kbd>+<kbd>Space</kbd>.
- The editor content is analyzed as text; it is never executed to compute suggestions.

### Safety

- **Nothing is overwritten silently.** "Generate and save" asks for confirmation if the file exists,
  and GTK's "Save as" dialog does too.
- **Timestamped backups** (`name.sh.YYYYMMDD-HHMMSS.bak`, or `-1`, `-2`… if one already exists)
  before overwriting. They are created with exclusive open and are never deleted automatically. In
  Studio, the original file is backed up the first time it is overwritten in a session; later saves
  of the same document do not create more backups.
- **Atomic writes** (temporary file + `os.replace`), keeping existing permissions. `x` is only added
  where `r` is already set. If the file changed on disk after you opened it, you are asked first.
- **Check syntax** runs `bash --noprofile --norc -n` on stdin, without `BASH_ENV`, with a time limit
  and in a background thread. It never runs the script. Correct syntax does **not** prove the script
  is safe.
- **Run** is a separate action that requires saving first. It shows the file, interpreter and
  directory, states that no privileges are elevated, lists potentially dangerous commands it found,
  and asks for confirmation. It uses an argument list (`shell=False`), a closed stdin and never
  `sudo`. It can be stopped (SIGTERM to the process group, SIGKILL after 3 s). **It is not a
  sandbox**: the script runs with your user's permissions.

## Installation

MKSCRIPT Studio runs straight from the downloaded folder: nothing to compile and no pip. It only
needs a few system packages, which the launcher can install for you.

### Requirements

- A recent Linux distribution with a desktop: Kali, Debian 13, Ubuntu 24.04, Fedora 39, Arch or
  later.
- Python 3.11+, GTK 4.10+, libadwaita 1.4+, GtkSourceView 5 and PyGObject. Step 2 installs them.
- `git` to download and update the program.
- Optional: `shellcheck`, for additional static analysis. Step 2 installs it as well.

Debian 12 and Ubuntu 22.04 ship a libadwaita that is too old and are not supported.

### 1. Download

```bash
git clone --depth 1 https://github.com/RaulDevStack/mkscript-studio.git
```

```bash
cd mkscript-studio
```

`--depth 1` downloads only the latest version, without history. If you do not have git, install it
first with `sudo apt install git` (or `dnf` / `pacman`).

### 2. Install the dependencies (first time only)

```bash
./mkscript-studio --install-deps
```

The launcher detects your package manager, shows the command it will run and asks for your sudo
password. If you prefer to install them yourself, these are the equivalent commands:

| Distribution | Command |
| --- | --- |
| Kali, Debian, Ubuntu | `sudo apt install python3 python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-gtksource-5 shellcheck` |
| Fedora | `sudo dnf install python3 python3-gobject gtk4 libadwaita gtksourceview5 ShellCheck` |
| Arch Linux | `sudo pacman -S python python-gobject gtk4 libadwaita gtksourceview5 shellcheck` |

To check that everything is ready without opening the application:

```bash
./mkscript-studio --check
```

### 3. Open the program

```bash
./mkscript-studio
```

You can also open a script directly:

```bash
./mkscript-studio ~/Scripts/backup.sh
```

Run it as your normal user, not as `root`: settings are stored in your home folder, and graphical
applications started as root often fail to open their window.

### 4. Add it to the application menu (optional)

```bash
bash packaging/install-desktop.sh
```

Creates a "MKSCRIPT Studio" entry in your desktop menu, no sudo needed. The entry points at this
folder: if you move it, run the command again.

### Update

Inside the program folder:

```bash
git pull
```

The next time you open it (from the terminal or the menu) it uses the new version.

### Uninstall

Delete the program folder and, if you added it to the menu, the menu entry and icon:

```bash
rm -f ~/.local/share/applications/io.github.rauldevstack.MkscriptStudio.desktop ~/.local/share/icons/hicolor/scalable/apps/io.github.rauldevstack.MkscriptStudio.svg
```

Your preferences live in `~/.config/mkscript-studio`, and recent files, projects and custom templates
in `~/.local/share/mkscript-studio`. Delete them only if you do not want to keep them. Scripts you
saved (in `~/Scripts` by default) are not touched.

### Troubleshooting

| Message | Solution |
| --- | --- |
| `Missing system dependencies` | Run `./mkscript-studio --install-deps`. |
| `versions that are too old` | Your distribution is older than the supported ones; you need a more recent one. |
| `Permission denied` when running `./mkscript-studio` | Use `bash mkscript-studio` or make it executable with `chmod +x mkscript-studio`. |
| `Could not resolve host: github.com` | The machine has no internet connection or DNS is not working. |
| `fatal: could not create work tree dir` | The current folder no longer exists; run `cd ~` and repeat the `git clone`. |

## Architecture

```text
src/mkscript_studio/
├── app.py, window.py          # Adw.Application, window, navigation, actions and shortcuts
├── context.py                 # Shared services (no GTK)
├── paths.py, persistence.py   # XDG paths, ~, atomic JSON and quarantine of damaged files
├── preferences/               # Validated model + store with change notifications
├── generator/                 # Models, deterministic engine, templates and Bash escaping
├── editor/
│   ├── completion.py          # Autocompletion engine and Tab logic (pure Python)
│   ├── bash_words.py          # Vocabulary with descriptions
│   └── code_editor.py         # GtkSourceView + suggestion list + find/replace
├── services/                  # Files, backups, validation, execution, processes, projects, state
├── ui/                        # Views: home, library, studio, projects, preferences
└── resources/                 # CSS (graphite/teal palette), editor color schemes, icon
```

Everything outside `app.py`, `window.py`, `editor/code_editor.py` and `ui/` works without GTK.

### Technical decisions

- **GTK 4 + libadwaita + GtkSourceView 5**: a native GNOME application. GtkSourceView provides
  highlighting, line numbers, indentation, undo and search maintained by GNOME, so there was no need
  to write an editor from scratch.
- **Custom suggestion list** (a `Gtk.Popover` that does not steal focus) instead of
  `GtkSourceCompletion`. The required Tab behavior (complete directly when there is a single match,
  indent when there is none) needs full keyboard control. The logic lives in
  `completion.resolve_tab`, which is pure Python.
- **JSON** for preferences, state, projects and custom templates. The data is small and stays
  readable and editable by hand; SQLite added nothing. Damaged files are moved aside
  (`*.corrupt-DATE`) instead of deleted.
- **Bash only**: the shell selector only offers Bash and says why. Zsh and POSIX sh will be added
  once the engine can generate correct code for them.
- **Palette**: with libadwaita ≥ 1.6 CSS variables are redefined; with older versions, named colors.
  The graphite palette is only loaded in dark mode.

## Packaging (future)

- **Flatpak** (recommended): `org.gnome.Platform` ≥ 46 runtime, which already includes GTK 4,
  libadwaita and GtkSourceView 5. It needs a manifest with a Python module, `finish-args` for home
  access (`--filesystem=home`) and running on the host with `flatpak-spawn --host` so scripts see
  the real system.
- **Distribution packages**: the `.desktop` launcher and SVG icon already exist. An AppStream
  `metainfo.xml` file is still missing.

## Known limitations

- **The GUI has not been run on Linux yet.** This version was written on a Windows machine without
  GTK. The non-GUI core is covered by automated tests; the GUI was checked statically against the
  PyGObject type stubs (method names, properties and signatures), but nobody has opened it yet.
  There may be layout or behavior issues that only show up when running it.
- Single-document editor (no tabs).
- The risk analysis before running is approximate (regular expressions). It is not a security
  analysis.
- Execution has no interactive input (stdin is closed). Scripts that use `read` or `sudo` without a
  terminal will fail at that command.
- In Studio, the backup is made once per document and session, not on every save.
- No configuration profiles yet. The preferences model is flat and versioned so they can be added.
- Placeholder icon, created for this project.
- The interface is in English only; translations are not supported yet.

## License

MIT. See [LICENSE](LICENSE).
