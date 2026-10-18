"""Main window: adaptive side navigation and the five sections."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, GObject, Gtk  # noqa: E402

from mkscript_studio import APP_NAME  # noqa: E402
from mkscript_studio.context import AppServices  # noqa: E402
from mkscript_studio.ui.home_view import HomeView  # noqa: E402
from mkscript_studio.ui.library_view import LibraryView  # noqa: E402
from mkscript_studio.ui.preferences_view import PreferencesView  # noqa: E402
from mkscript_studio.ui.projects_view import ProjectsView  # noqa: E402
from mkscript_studio.ui.studio_view import StudioView  # noqa: E402

SECTIONS = [
    ("home", "Home", "go-home-symbolic"),
    ("library", "Library", "view-grid-symbolic"),
    ("studio", "Studio", "text-x-generic-symbolic"),
    ("projects", "Projects", "folder-symbolic"),
    ("preferences", "Preferences", "emblem-system-symbolic"),
]

_SHORTCUTS_UI = """
<interface>
  <object class="GtkShortcutsWindow" id="shortcuts">
    <property name="modal">true</property>
    <child>
      <object class="GtkShortcutsSection">
        <property name="section-name">main</property>
        {groups}
      </object>
    </child>
  </object>
</interface>
"""

_SHORTCUT_GROUPS = [
    ("Files", [("&lt;Ctrl&gt;n", "New script"), ("&lt;Ctrl&gt;o", "Open"), ("&lt;Ctrl&gt;s", "Save"),
               ("&lt;Ctrl&gt;&lt;Shift&gt;s", "Save as")]),
    ("Editing", [("&lt;Ctrl&gt;z", "Undo"), ("&lt;Ctrl&gt;&lt;Shift&gt;z", "Redo"),
                 ("&lt;Ctrl&gt;f", "Find"), ("&lt;Ctrl&gt;h", "Replace"), ("&lt;Ctrl&gt;l", "Go to line"),
                 ("Tab", "Autocomplete or indent"), ("&lt;Ctrl&gt;space", "Show suggestions"),
                 ("Escape", "Close suggestions")]),
    ("Check and run", [("F7", "Check syntax"), ("&lt;Shift&gt;F7", "ShellCheck"),
                       ("F5", "Run (with confirmation)")]),
    ("Navigation", [("&lt;Ctrl&gt;1", "Home"), ("&lt;Ctrl&gt;2", "Library"), ("&lt;Ctrl&gt;3", "Studio"),
                    ("&lt;Ctrl&gt;4", "Projects"), ("&lt;Ctrl&gt;comma", "Preferences"),
                    ("&lt;Ctrl&gt;question", "Keyboard shortcuts")]),
]


def _shortcuts_window() -> Gtk.Widget | None:
    groups = []
    for title, items in _SHORTCUT_GROUPS:
        shortcuts = "".join(
            f'<child><object class="GtkShortcutsShortcut"><property name="accelerator">{accel}</property>'
            f'<property name="title">{text}</property></object></child>'
            for accel, text in items
        )
        groups.append(f'<child><object class="GtkShortcutsGroup"><property name="title">{title}</property>'
                      f"{shortcuts}</object></child>")
    try:
        builder = Gtk.Builder.new_from_string(_SHORTCUTS_UI.format(groups="".join(groups)), -1)
    except GLib.Error:
        return None
    return builder.get_object("shortcuts")


class MainWindow(Adw.ApplicationWindow):
    __gtype_name__ = "MksMainWindow"

    def __init__(self, app: Adw.Application, services: AppServices) -> None:
        super().__init__(application=app, title=APP_NAME, default_width=1320, default_height=840)
        self.set_size_request(360, 480)
        self.services = services
        self._section = "home"
        self._document = ("", False)
        self._close_confirmed = False
        self._selecting = False

        # Whatever the views use while being built must exist before them.
        self.toast_overlay = Adw.ToastOverlay()
        self.window_title = Adw.WindowTitle(title="Home")

        self.studio = StudioView(self, services)
        self.library = LibraryView(self, services)
        self.home = HomeView(self, services)
        self.projects = ProjectsView(self, services)
        self.preferences = PreferencesView(self, services)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=120)
        for name, view in (("home", self.home), ("library", self.library), ("studio", self.studio),
                           ("projects", self.projects), ("preferences", self.preferences)):
            self.stack.add_named(view, name)

        self.split_view = Adw.OverlaySplitView(min_sidebar_width=200, max_sidebar_width=240)
        self.split_view.set_sidebar(self._build_sidebar())
        self.split_view.set_content(self._build_content())
        self.toast_overlay.set_child(self.split_view)
        self.set_content(self.toast_overlay)

        breakpoint_ = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 860sp"))
        breakpoint_.add_setter(self.split_view, "collapsed", True)
        self.add_breakpoint(breakpoint_)

        self._install_actions()
        overlay = _shortcuts_window()
        if overlay is not None:
            self.set_help_overlay(overlay)
        self.connect("close-request", self._on_close_request)
        self.show_section("home")

    # -- construction ------------------------------------------------------

    def _build_sidebar(self) -> Gtk.Widget:
        self.nav_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.nav_list.add_css_class("navigation-sidebar")
        self.nav_list.update_property([Gtk.AccessibleProperty.LABEL], ["Sections"])
        for _name, title, icon in SECTIONS:
            box = Gtk.Box(spacing=12, margin_top=8, margin_bottom=8, margin_start=6)
            box.append(Gtk.Image(icon_name=icon))
            box.append(Gtk.Label(label=title, xalign=0))
            row = Gtk.ListBoxRow()
            row.set_child(box)
            row.update_property([Gtk.AccessibleProperty.LABEL], [title])
            self.nav_list.append(row)
        self.nav_list.connect("row-selected", self._on_nav_selected)

        brand = Gtk.Label(label="MKSCRIPT <span weight='400' alpha='60%'>Studio</span>", use_markup=True)
        brand.add_css_class("mks-brand")
        header = Adw.HeaderBar(show_title=True)
        header.set_title_widget(brand)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroller.set_child(self.nav_list)
        toolbar.set_content(scroller)
        return toolbar

    def _build_content(self) -> Gtk.Widget:
        header = Adw.HeaderBar()
        header.set_title_widget(self.window_title)
        sidebar_button = Gtk.ToggleButton(icon_name="sidebar-show-symbolic", tooltip_text="Show sections")
        flags = GObject.BindingFlags.SYNC_CREATE | GObject.BindingFlags.BIDIRECTIONAL
        self.split_view.bind_property("show-sidebar", sidebar_button, "active", flags)
        self.split_view.bind_property("collapsed", sidebar_button, "visible", GObject.BindingFlags.SYNC_CREATE)
        header.pack_start(sidebar_button)

        menu = Gio.Menu()
        document_section = Gio.Menu()
        document_section.append("Save as template…", "win.save-template")
        document_section.append("Go to line…", "win.goto-line")
        menu.append_section(None, document_section)
        app_section = Gio.Menu()
        app_section.append("Keyboard shortcuts", "win.show-help-overlay")
        app_section.append("About MKSCRIPT Studio", "app.about")
        app_section.append("Quit", "app.quit")
        menu.append_section(None, app_section)
        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, tooltip_text="Main menu",
                                     primary=True)
        header.pack_end(menu_button)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(self.stack)
        return toolbar

    def _install_actions(self) -> None:
        studio = self.studio
        actions = {
            "new": lambda: (self.show_section("studio"), studio.new_document()),
            "open": studio.open_dialog,
            "save": lambda: (self.show_section("studio"), studio.save()),
            "save-as": lambda: (self.show_section("studio"), studio.save_as()),
            "find": studio.show_search,
            "replace": lambda: studio.show_search(replace=True),
            "goto-line": lambda: (self.show_section("studio"), studio.go_to_line()),
            "check-syntax": studio.check_syntax,
            "shellcheck": studio.run_shellcheck,
            "run": studio.run_script,
            "stop": studio.stop_script,
            "save-template": lambda: (self.show_section("studio"), studio.save_as_template()),
        }
        for name, _title, _icon in SECTIONS:
            actions[f"section-{name}"] = lambda n=name: self.show_section(n)
        for name, callback in actions.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, cb=callback: cb())
            self.add_action(action)

    # -- navigation ------------------------------------------------------------

    def show_section(self, name: str) -> None:
        index = next(i for i, (n, _t, _i) in enumerate(SECTIONS) if n == name)
        self._section = name
        self.stack.set_visible_child_name(name)
        if not self._selecting:
            self._selecting = True
            self.nav_list.select_row(self.nav_list.get_row_at_index(index))
            self._selecting = False
        if self.split_view.get_collapsed():
            self.split_view.set_show_sidebar(False)
        self._update_title()
        if name == "studio":
            self.studio.editor.focus()

    def _on_nav_selected(self, _list, row: Gtk.ListBoxRow | None) -> None:
        if row is None or self._selecting:
            return
        self._selecting = True
        self.show_section(SECTIONS[row.get_index()][0])
        self._selecting = False

    def set_document_title(self, name: str, modified: bool) -> None:
        self._document = (name, modified)
        self._update_title()

    def _update_title(self) -> None:
        name, modified = self._document
        section_title = next(t for n, t, _i in SECTIONS if n == self._section)
        self.window_title.set_title(section_title)
        self.window_title.set_subtitle(f"{'● ' if modified else ''}{name}" if self._section == "studio" else "")
        document = f"{name}{' •' if modified else ''} — " if name else ""
        self.set_title(f"{document}{APP_NAME}")

    # -- helpers for the views ----------------------------------------------------

    def toast(self, message: str) -> None:
        toast = Adw.Toast(title=GLib.markup_escape_text(message), timeout=4)
        self.toast_overlay.add_toast(toast)

    def templates_changed(self) -> None:
        self.studio.reload_templates()
        self.library.refresh()
        self.home.refresh()

    # -- closing -----------------------------------------------------------------

    def _on_close_request(self, _window) -> bool:
        if self._close_confirmed or not self.studio.has_unsaved_changes():
            self.studio.shutdown()
            return False

        def proceed() -> None:
            self._close_confirmed = True
            self.close()

        self.show_section("studio")
        self.studio.confirm_discard(proceed)
        return True
