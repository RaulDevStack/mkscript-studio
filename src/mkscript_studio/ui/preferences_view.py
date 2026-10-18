"""Preferencias: cada cambio se valida y se guarda en el momento."""

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

        # -- Generación --------------------------------------------------------
        generation = Adw.PreferencesGroup(
            title="Generación",
            description="Se aplica a los scripts generados desde plantillas y a la vista previa.")
        shell_row = self._combo("Shell", SUPPORTED_SHELLS, "shell")
        shell_row.set_subtitle("Esta versión solo genera Bash. Zsh y POSIX sh no están disponibles todavía, "
                               "para no producir scripts incorrectos.")
        generation.add(shell_row)
        generation.add(self._combo("Estilo de comentarios", COMMENT_STYLES, "comment_style"))
        generation.add(self._switch(
            "Modo estricto", "strict_mode",
            "Añade set -Eeuo pipefail: el script se detiene ante errores, variables sin definir y fallos en "
            "tuberías. Puede cambiar el comportamiento de scripts que usan códigos de salida en su lógica."))
        generation.add(self._switch("Colores en los mensajes", "color_output",
                                    "Solo en terminal; se desactivan con la variable NO_COLOR."))
        self.add(generation)

        # -- Archivos ------------------------------------------------------------
        files = Adw.PreferencesGroup(title="Archivos")
        self.output_row = Adw.EntryRow(title="Carpeta de salida", show_apply_button=True)
        self.output_row.set_text(prefs.output_directory)
        self.output_row.connect("apply", lambda row: self._apply_path_row(row, "output_directory"))
        self.output_row.add_suffix(self._folder_button(self.output_row, "output_directory"))
        files.add(self.output_row)
        files.add(self._switch("Permisos de ejecución", "executable_permissions",
                               "Añade el permiso x al guardar (sin quitar permisos existentes)."))
        files.add(self._switch("Abrir tras generar", "open_after_generate",
                               "Al usar «Generar y guardar», abre el archivo en Studio."))
        self.add(files)

        backups = Adw.PreferencesGroup(
            title="Copias de seguridad",
            description="Las copias se nombran con fecha y hora (nombre.sh.AAAAMMDD-HHMMSS.bak) "
                        "y nunca se borran automáticamente.")
        backups.add(self._switch("Copia antes de sobrescribir", "backup_before_overwrite",
                                 "Si se desactiva, sobrescribir un archivo reemplaza su contenido sin copia."))
        self.backup_row = Adw.EntryRow(title="Carpeta de copias (vacío = junto al archivo)", show_apply_button=True)
        self.backup_row.set_text(prefs.backup_directory)
        self.backup_row.connect("apply", lambda row: self._apply_path_row(row, "backup_directory", allow_empty=True))
        self.backup_row.add_suffix(self._folder_button(self.backup_row, "backup_directory"))
        backups.add(self.backup_row)
        self.add(backups)

        # -- Editor --------------------------------------------------------------
        editor = Adw.PreferencesGroup(title="Editor")
        editor.add(self._spin("Ancho de tabulación", "editor_tab_width", 1, 16))
        editor.add(self._switch("Insertar espacios en lugar de tabulaciones", "editor_insert_spaces"))
        editor.add(self._switch("Números de línea", "editor_show_line_numbers"))
        editor.add(self._switch("Resaltar la línea actual", "editor_highlight_current_line"))
        editor.add(self._switch("Margen derecho (columna 100)", "editor_show_right_margin"))
        editor.add(self._switch("Ajuste de línea", "editor_wrap_text"))
        editor.add(self._spin("Tamaño de letra (pt)", "editor_font_size", 6, 48))
        self.add(editor)

        completion = Adw.PreferencesGroup(
            title="Autocompletado",
            description="Tab completa la palabra si solo hay una coincidencia y muestra una lista si hay varias.")
        completion.add(self._switch("Autocompletado", "completion_enabled"))
        completion.add(self._switch("Sugerir mientras se escribe", "completion_while_typing"))
        completion.add(self._spin("Caracteres mínimos para sugerir", "completion_min_chars", 1, 10))
        completion.add(self._switch(
            "Incluir órdenes instaladas", "completion_system_commands",
            "Añade los ejecutables del PATH. Puede haber más coincidencias y Tab mostrará una lista con más frecuencia."))
        self.add(completion)

        appearance = Adw.PreferencesGroup(title="Apariencia")
        appearance.add(self._combo("Tema", THEMES, "theme"))
        reset = Adw.ActionRow(title="Restablecer preferencias",
                              subtitle="Vuelve a los valores predeterminados.")
        reset_button = Gtk.Button(label="Restablecer", valign=Gtk.Align.CENTER)
        reset_button.add_css_class("destructive-action")
        reset_button.connect("clicked", lambda *_: self._confirm_reset())
        reset.add_suffix(reset_button)
        appearance.add(reset)
        self.add(appearance)

    # -- filas enlazadas a preferencias ----------------------------------------

    def _save(self, **changes: Any) -> bool:
        try:
            self.store.update(**changes)
        except (ValueError, OSError) as exc:
            self.window.toast(f"No se guardó la preferencia: {exc}")
            return False
        return True

    def _follow(self, key: str, current, apply) -> None:
        """Mantiene el control sincronizado si la preferencia cambia desde otro sitio."""

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
            self.window.toast(f"Carpeta actualizada: {text} (se creará cuando haga falta)")

    def _folder_button(self, row: Adw.EntryRow, key: str) -> Gtk.Button:
        button = Gtk.Button(icon_name="folder-open-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Elegir carpeta")
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
                self.window.toast("Preferencias restablecidas")

        ask(self, "¿Restablecer las preferencias?", "Todas las opciones volverán a sus valores predeterminados.",
            [("cancel", "Cancelar", ""), ("reset", "Restablecer", "destructive")], on_response)
