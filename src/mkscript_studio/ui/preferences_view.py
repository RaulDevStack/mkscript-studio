"""Preferences: every change is validated and saved immediately."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from mkscript_studio.paths import expand_user_path  # noqa: E402
from mkscript_studio.preferences.models import COMMENT_STYLES, SUPPORTED_SHELLS, THEMES  # noqa: E402
from mkscript_studio.ui.common import ask, choose_folder, tilde_path  # noqa: E402

if TYPE_CHECKING:
    from mkscript_studio.context import AppServices
    from mkscript_studio.window import MainWindow


class PreferencesView(Adw.PreferencesPage):
    __gtype_name__ = "MksPreferencesView"

    def __init__(self, window: MainWindow, services: AppServices) -> None:
        super().__init__()
        self.window = window
        self.services = services
        self.store = services.prefs
        prefs = self.store.preferences

        # -- Generation --------------------------------------------------------
        generation = Adw.PreferencesGroup(
            title="Generation",
            description="Applies to scripts generated from templates and to the preview.")
        shell_row = self._combo("Shell", SUPPORTED_SHELLS, "shell")
        shell_row.set_subtitle("This version only generates Bash. Zsh and POSIX sh are not available yet, "
                               "to avoid producing incorrect scripts.")
        generation.add(shell_row)
        generation.add(self._combo("Comment style", COMMENT_STYLES, "comment_style"))
        generation.add(self._switch(
            "Strict mode", "strict_mode",
            "Adds set -Eeuo pipefail: the script stops on errors, unset variables and pipeline failures. "
            "It can change the behavior of scripts that use exit codes as part of their logic."))
        generation.add(self._switch("Colored messages", "color_output",
                                    "Only on a terminal; disabled by the NO_COLOR variable."))
        self.add(generation)

        # -- Files ---------------------------------------------------------------
        files = Adw.PreferencesGroup(title="Files")
        self.output_row = Adw.EntryRow(title="Output folder", show_apply_button=True)
        self.output_row.set_text(prefs.output_directory)
        self.output_row.connect("apply", lambda row: self._apply_path_row(row, "output_directory"))
        self.output_row.add_suffix(self._folder_button(self.output_row, "output_directory"))
        files.add(self.output_row)
        files.add(self._switch("Executable permission", "executable_permissions",
                               "Adds the x permission when saving (never removes existing permissions)."))
        files.add(self._switch("Open after generating", "open_after_generate",
                               "After \"Generate and save\", open the file in Studio."))
        self.add(files)

        backups = Adw.PreferencesGroup(
            title="Backups",
            description="Backups are named with the date and time (name.sh.YYYYMMDD-HHMMSS.bak) "
                        "and are never deleted automatically.")
        backups.add(self._switch("Backup before overwriting", "backup_before_overwrite",
                                 "When off, overwriting a file replaces its content without a backup."))
        self.backup_row = Adw.EntryRow(title="Backup folder (empty = next to the file)", show_apply_button=True)
        self.backup_row.set_text(prefs.backup_directory)
        self.backup_row.connect("apply", lambda row: self._apply_path_row(row, "backup_directory", allow_empty=True))
        self.backup_row.add_suffix(self._folder_button(self.backup_row, "backup_directory"))
        backups.add(self.backup_row)
        self.add(backups)

        # -- Editor --------------------------------------------------------------
        editor = Adw.PreferencesGroup(title="Editor")
        editor.add(self._spin("Tab width", "editor_tab_width", 1, 16))
        editor.add(self._switch("Insert spaces instead of tabs", "editor_insert_spaces"))
        editor.add(self._switch("Line numbers", "editor_show_line_numbers"))
        editor.add(self._switch("Highlight current line", "editor_highlight_current_line"))
        editor.add(self._switch("Right margin (column 100)", "editor_show_right_margin"))
        editor.add(self._switch("Wrap lines", "editor_wrap_text"))
        editor.add(self._spin("Font size (pt)", "editor_font_size", 6, 48))
        self.add(editor)

        completion = Adw.PreferencesGroup(
            title="Autocompletion",
            description="Tab completes the word when there is a single match and shows a list when there are several.")
        completion.add(self._switch("Autocompletion", "completion_enabled"))
        completion.add(self._switch("Suggest while typing", "completion_while_typing"))
        completion.add(self._spin("Minimum characters to suggest", "completion_min_chars", 1, 10))
        completion.add(self._switch(
            "Include installed commands", "completion_system_commands",
            "Adds the executables on PATH. There may be more matches, so Tab will show a list more often."))
        self.add(completion)

        appearance = Adw.PreferencesGroup(title="Appearance")
        appearance.add(self._combo("Theme", THEMES, "theme"))
        reset = Adw.ActionRow(title="Reset preferences",
                              subtitle="Go back to the default values.")
        reset_button = Gtk.Button(label="Reset", valign=Gtk.Align.CENTER)
        reset_button.add_css_class("destructive-action")
        reset_button.connect("clicked", lambda *_: self._confirm_reset())
        reset.add_suffix(reset_button)
        appearance.add(reset)
        self.add(appearance)

    # -- rows bound to preferences -------------------------------------------

    def _save(self, **changes: Any) -> bool:
        try:
            self.store.update(**changes)
        except (ValueError, OSError) as exc:
            self.window.toast(f"The preference was not saved: {exc}")
            return False
        return True

    def _follow(self, key: str, current, apply) -> None:
        """Keep the control in sync if the preference changes elsewhere."""

        def listener(prefs, changed: set[str]) -> None:
            if key in changed and current() != getattr(prefs, key):
                apply(getattr(prefs, key))

        self.store.subscribe(listener)

    def _switch(self, title: str, key: str, subtitle: str = "") -> Adw.SwitchRow:
        row = Adw.SwitchRow(title=title, subtitle=subtitle, active=getattr(self.store.preferences, key))
        row.connect("notify::active", lambda r, _p: self._save(**{key: r.get_active()}))
        self._follow(key, row.get_active, row.set_active)
        return row

    def _spin(self, title: str, key: str, low: int, high: int) -> Adw.SpinRow:
        row = Adw.SpinRow.new_with_range(low, high, 1)
        row.set_title(title)
        row.set_value(getattr(self.store.preferences, key))
        row.connect("notify::value", lambda r, _p: self._save(**{key: int(r.get_value())}))
        self._follow(key, lambda: int(row.get_value()), row.set_value)
        return row

    def _combo(self, title: str, options: dict[str, str], key: str) -> Adw.ComboRow:
        keys = list(options)
        row = Adw.ComboRow(title=title, model=Gtk.StringList.new(list(options.values())))
        current = getattr(self.store.preferences, key)
        row.set_selected(keys.index(current) if current in keys else 0)
        row.connect("notify::selected", lambda r, _p: self._save(**{key: keys[r.get_selected()]}))
        self._follow(key, lambda: keys[row.get_selected()], lambda value: row.set_selected(keys.index(value)))
        return row

    def _apply_path_row(self, row: Adw.EntryRow, key: str, allow_empty: bool = False) -> None:
        text = row.get_text().strip()
        if not text and allow_empty:
            self._save(**{key: ""})
            return
        try:
            expand_user_path(text)
        except ValueError as exc:
            row.add_css_class("error")
            self.window.toast(str(exc))
            return
        row.remove_css_class("error")
        if self._save(**{key: text}):
            self.window.toast(f"Folder updated: {text} (it will be created when needed)")

    def _folder_button(self, row: Adw.EntryRow, key: str) -> Gtk.Button:
        button = Gtk.Button(icon_name="folder-open-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Choose folder")
        button.add_css_class("flat")

        def chosen(path) -> None:
            row.set_text(tilde_path(path))
            self._apply_path_row(row, key)

        def pick(*_args) -> None:
            current = row.get_text().strip()
            try:
                start = expand_user_path(current) if current else None
            except ValueError:
                start = None
            choose_folder(self.window, chosen, start)

        button.connect("clicked", pick)
        return button

    def _confirm_reset(self) -> None:
        def on_response(response: str) -> None:
            if response == "reset":
                self.store.reset()
                self.output_row.set_text(self.store.preferences.output_directory)
                self.backup_row.set_text(self.store.preferences.backup_directory)
                self.window.toast("Preferences reset")

        ask(self, "Reset preferences?", "All options will go back to their default values.",
            [("cancel", "Cancel", ""), ("reset", "Reset", "destructive")], on_response)
