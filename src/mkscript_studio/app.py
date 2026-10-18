"""GTK application entry point."""

from __future__ import annotations

import sys
from pathlib import Path

try:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    gi.require_version("GtkSource", "5")
    from gi.repository import Adw, Gdk, Gio, Gtk, GtkSource
except (ImportError, ValueError) as exc:  # missing system dependencies
    _IMPORT_ERROR: Exception | None = exc
else:
    _IMPORT_ERROR = None

from mkscript_studio import APP_ID, APP_NAME, __version__
from mkscript_studio.paths import resources_dir

_MISSING_DEPS = """\
MKSCRIPT Studio needs PyGObject, GTK 4, libadwaita (>= 1.4) and GtkSourceView 5.
Error: {error}

Install on Debian/Ubuntu/Kali:
  sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-gtksource-5
Fedora:
  sudo dnf install python3-gobject gtk4 libadwaita gtksourceview5
Arch Linux:
  sudo pacman -S python-gobject gtk4 libadwaita gtksourceview5
"""

ACCELS = {
    "win.new": ["<Control>n"],
    "win.open": ["<Control>o"],
    "win.save": ["<Control>s"],
    "win.save-as": ["<Control><Shift>s"],
    "win.find": ["<Control>f"],
    "win.replace": ["<Control>h"],
    "win.goto-line": ["<Control>l"],
    "win.check-syntax": ["F7"],
    "win.shellcheck": ["<Shift>F7"],
    "win.run": ["F5"],
    "win.section-home": ["<Control>1"],
    "win.section-library": ["<Control>2"],
    "win.section-studio": ["<Control>3"],
    "win.section-projects": ["<Control>4"],
    "win.section-preferences": ["<Control>comma", "<Control>5"],
    "win.show-help-overlay": ["<Control>question"],
    "app.quit": ["<Control>q"],
}

_COLOR_SCHEMES = {"dark": "FORCE_DARK", "light": "FORCE_LIGHT", "system": "DEFAULT"}


def _load_css(provider, css: str) -> None:
    if hasattr(provider, "load_from_string"):  # GTK >= 4.12
        provider.load_from_string(css)
    else:
        provider.load_from_data(css, -1)


if _IMPORT_ERROR is None:
    from mkscript_studio.context import AppServices
    from mkscript_studio.window import MainWindow

    class Application(Adw.Application):
        def __init__(self) -> None:
            super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN)
            self.services: AppServices | None = None
            self._dark_provider = Gtk.CssProvider()
            self._font_provider = Gtk.CssProvider()
            self._dark_loaded = False

        def do_startup(self) -> None:
            Adw.Application.do_startup(self)
            GtkSource.init()
            Gtk.Window.set_default_icon_name(APP_ID)
            self.services = AppServices.create()
            self._setup_styles()
            self._add_action("quit", lambda: self.quit_safely())
            self._add_action("about", self._show_about)
            for action, accels in ACCELS.items():
                self.set_accels_for_action(action, accels)
            self.services.prefs.subscribe(lambda prefs, changed: self._apply_style_preferences())

        def _add_action(self, name: str, callback) -> None:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda *_: callback())
            self.add_action(action)

        def _setup_styles(self) -> None:
            display = Gdk.Display.get_default()
            styles = resources_dir() / "styles"
            Gtk.IconTheme.get_for_display(display).add_search_path(str(resources_dir() / "icons"))

            # libadwaita >= 1.6 uses CSS variables; older versions use named colors.
            modern = Adw.get_minor_version() >= 6
            base = Gtk.CssProvider()
            base.load_from_path(str(styles / ("accent-vars.css" if modern else "accent-named.css")))
            Gtk.StyleContext.add_provider_for_display(display, base, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            self._dark_provider.load_from_path(str(styles / ("dark-vars.css" if modern else "dark-named.css")))
            components = Gtk.CssProvider()
            components.load_from_path(str(styles / "style.css"))
            Gtk.StyleContext.add_provider_for_display(display, components, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            Gtk.StyleContext.add_provider_for_display(display, self._font_provider,
                                                      Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
            Adw.StyleManager.get_default().connect("notify::dark", lambda *_: self._sync_dark_palette())
            self._apply_style_preferences()

        def _apply_style_preferences(self) -> None:
            prefs = self.services.prefs.preferences
            scheme = getattr(Adw.ColorScheme, _COLOR_SCHEMES.get(prefs.theme, "FORCE_DARK"))
            Adw.StyleManager.get_default().set_color_scheme(scheme)
            _load_css(self._font_provider,
                      f".mks-editor textview {{ font-size: {prefs.editor_font_size}pt; }}\n")
            self._sync_dark_palette()

        def _sync_dark_palette(self) -> None:
            display = Gdk.Display.get_default()
            dark = Adw.StyleManager.get_default().get_dark()
            if dark and not self._dark_loaded:
                Gtk.StyleContext.add_provider_for_display(display, self._dark_provider,
                                                          Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            elif not dark and self._dark_loaded:
                Gtk.StyleContext.remove_provider_for_display(display, self._dark_provider)
            self._dark_loaded = dark

        def do_activate(self) -> None:
            window = self.props.active_window
            if window is None:
                window = MainWindow(self, self.services)
                warnings = self.services.startup_warnings()
                for warning in warnings[:3]:
                    window.toast(warning)
            window.present()

        def do_open(self, files, _n_files, _hint) -> None:
            self.do_activate()
            window = self.props.active_window
            for gfile in files[:1]:
                path = gfile.get_path()
                if path:
                    window.studio.open_path(Path(path))

        def quit_safely(self) -> None:
            window = self.props.active_window
            if window is not None:
                window.close()  # goes through the unsaved-changes confirmation
            else:
                self.quit()

        def _show_about(self) -> None:
            about = Adw.AboutWindow(
                transient_for=self.props.active_window,
                application_name=APP_NAME,
                application_icon=APP_ID,
                version=__version__,
                developer_name="RaulDevStack",
                license_type=Gtk.License.MIT_X11,
                website="https://github.com/RaulDevStack/mkscript-studio",
                comments="A visual workshop to create, edit, validate and save Bash scripts.\n"
                         "The evolution of mkscript.",
            )
            about.present()


def main(argv: list[str] | None = None) -> int:
    if _IMPORT_ERROR is not None:
        sys.stderr.write(_MISSING_DEPS.format(error=_IMPORT_ERROR))
        return 1
    app = Application()
    return app.run(argv if argv is not None else sys.argv)


if __name__ == "__main__":
    sys.exit(main())
