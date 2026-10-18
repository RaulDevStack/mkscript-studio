"""Home: create scripts, recent files, favorites, shortcuts and current preferences."""

from __future__ import annotations

from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from mkscript_studio import APP_NAME  # noqa: E402
from mkscript_studio.preferences.models import COMMENT_STYLES, SUPPORTED_SHELLS, THEMES  # noqa: E402
from mkscript_studio.ui.common import label, text_button, tilde_path  # noqa: E402

if TYPE_CHECKING:
    from mkscript_studio.context import AppServices
    from mkscript_studio.window import MainWindow

SHORTCUTS = [
    ("Ctrl+N", "New script"),
    ("Ctrl+O", "Open file"),
    ("Ctrl+S", "Save"),
    ("Ctrl+F / Ctrl+H", "Find / replace"),
    ("Tab", "Autocomplete (ec → echo)"),
    ("Ctrl+Space", "Show suggestions"),
    ("F7", "Check syntax"),
    ("F5", "Run (with confirmation)"),
]


class HomeView(Gtk.ScrolledWindow):
    __gtype_name__ = "MksHomeView"

    def __init__(self, window: MainWindow, services: AppServices) -> None:
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.window = window
        self.services = services
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24,
                               margin_start=24, margin_end=24, margin_top=32, margin_bottom=32)
        clamp = Adw.Clamp(maximum_size=960, tightening_threshold=700)
        clamp.set_child(self.content)
        self.set_child(clamp)
        self.connect("map", lambda *_: self.refresh())
        services.prefs.subscribe(lambda *_: self.refresh())
        self.refresh()

    def refresh(self) -> None:
        child = self.content.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self.content.remove(child)
            child = following
        self.content.append(self._hero())
        columns = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=2,
                              min_children_per_line=1, column_spacing=24, row_spacing=24, homogeneous=True)
        columns.add_css_class("mks-flow")
        columns.append(self._recent_group())
        columns.append(self._favorites_group())
        columns.append(self._shortcuts_group())
        columns.append(self._preferences_group())
        self.content.append(columns)

    def _hero(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.add_css_class("mks-hero")
        box.append(label(APP_NAME, css=("title-1",)))
        box.append(label("Create, customize, validate and save Bash scripts. Start from a template "
                         "or write straight in the editor.", css=("dim-label",), wrap=True))
        buttons = Gtk.Box(spacing=8, margin_top=8)
        buttons.append(text_button("New script", self._new_script, icon="document-new-symbolic",
                                   css=("pill", "suggested-action")))
        buttons.append(text_button("From template", lambda: self.window.show_section("library"),
                                   icon="view-grid-symbolic", css=("pill",)))
        buttons.append(text_button("Open file…", self.window.studio.open_dialog,
                                   icon="document-open-symbolic", css=("pill",)))
        box.append(buttons)
        return box

    def _new_script(self) -> None:
        self.window.show_section("studio")
        self.window.studio.new_document()

    @staticmethod
    def _group(title: str, description: str = "") -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title=title, description=description or None, valign=Gtk.Align.START)
        return group

    def _recent_group(self) -> Gtk.Widget:
        group = self._group("Recent")
        recent = self.services.state.existing_recent_files()[:6]
        if not recent:
            group.add(Adw.ActionRow(title="No recent files yet",
                                    subtitle="Scripts you open or save will show up here."))
        for path in recent:
            row = Adw.ActionRow(title=path.name, subtitle=tilde_path(path.parent), activatable=True)
            row.set_use_markup(False)
            row.add_prefix(Gtk.Image(icon_name="utilities-terminal-symbolic"))
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.connect("activated", lambda _r, p=path: self.window.studio.open_path(p))
            group.add(row)
        return group

    def _favorites_group(self) -> Gtk.Widget:
        group = self._group("Favorite templates")
        favorites = [t for t in (self.services.templates.get(i) for i in self.services.state.favorite_templates) if t]
        if not favorites:
            group.add(Adw.ActionRow(title="No favorites",
                                    subtitle="Star templates in the Library."))
        for template in favorites[:6]:
            row = Adw.ActionRow(title=template.name, subtitle=template.category, activatable=True)
            row.set_use_markup(False)
            row.add_prefix(Gtk.Image(icon_name="starred-symbolic"))
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.connect("activated", lambda _r, t=template: self._open_template(t.id))
            group.add(row)
        return group

    def _open_template(self, template_id: str) -> None:
        self.window.show_section("library")
        self.window.library.select_template(template_id)

    def _shortcuts_group(self) -> Gtk.Widget:
        group = self._group("Shortcuts")
        for keys, action in SHORTCUTS:
            row = Adw.ActionRow(title=action)
            shortcut = label(keys, css=("mks-kbd",))
            shortcut.set_valign(Gtk.Align.CENTER)
            row.add_suffix(shortcut)
            group.add(row)
        return group

    def _preferences_group(self) -> Gtk.Widget:
        prefs = self.services.prefs.preferences
        group = self._group("Current preferences")
        button = Gtk.Button(label="Change", valign=Gtk.Align.CENTER)
        button.add_css_class("flat")
        button.connect("clicked", lambda *_: self.window.show_section("preferences"))
        group.set_header_suffix(button)
        rows = [
            ("Shell", SUPPORTED_SHELLS.get(prefs.shell, prefs.shell)),
            ("Comments", COMMENT_STYLES.get(prefs.comment_style, prefs.comment_style)),
            ("Strict mode", "On (set -Eeuo pipefail)" if prefs.strict_mode else "Off"),
            ("Output folder", prefs.output_directory),
            ("Backup before overwriting", "Yes" if prefs.backup_before_overwrite else "No"),
            ("Executable permission", "Yes" if prefs.executable_permissions else "No"),
            ("Theme", THEMES.get(prefs.theme, prefs.theme)),
        ]
        for title, value in rows:
            row = Adw.ActionRow(title=title, subtitle=value)
            row.set_use_markup(False)
            group.add(row)
        return group
