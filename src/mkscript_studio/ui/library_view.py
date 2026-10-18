"""Biblioteca: plantillas por categorías, búsqueda, favoritos, formulario y vista previa."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from mkscript_studio.editor.code_editor import CodeEditor  # noqa: E402
from mkscript_studio.generator.engine import GenerationError, generate_script  # noqa: E402
from mkscript_studio.generator.models import GenerationConfig, Template  # noqa: E402
from mkscript_studio.services.file_service import (  # noqa: E402
    FileExistsConflict,
    FileServiceError,
    InvalidNameError,
    normalize_script_name,
)
from mkscript_studio.ui.common import (  # noqa: E402
    ask,
    clear_container,
    label,
    show_error,
    text_button,
    tilde_path,
)
from mkscript_studio.ui.template_form import TemplateForm, risk_banner  # noqa: E402

if TYPE_CHECKING:
    from mkscript_studio.context import AppServices
    from mkscript_studio.window import MainWindow

ALL_CATEGORIES = "Todas las categorías"


class LibraryView(Gtk.Box):
    __gtype_name__ = "MksLibraryView"

    def __init__(self, window: MainWindow, services: AppServices) -> None:
        super().__init__()
        self.window = window
        self.services = services
        self.template: Template | None = None
        self._visible: list[Template] = []
        self._preview_source = 0
        self._loading = False

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, vexpand=True)
        paned.set_start_child(self._build_list_column())
        paned.set_end_child(self._build_detail_column())
        paned.set_resize_start_child(False)
        paned.set_shrink_start_child(False)
        paned.set_position(320)
        self.append(paned)

        services.prefs.subscribe(lambda *_: self._schedule_preview())
        Adw.StyleManager.get_default().connect("notify::dark", lambda m, _p: self.preview.set_dark(m.get_dark()))
        self.refresh()

    # -- construcción --------------------------------------------------------

    def _build_list_column(self) -> Gtk.Widget:
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, width_request=280,
                         margin_start=12, margin_end=12, margin_top=12, margin_bottom=12)
        self.search_entry = Gtk.SearchEntry(placeholder_text="Buscar plantillas")
        self.search_entry.connect("search-changed", lambda *_: self.refresh())
        column.append(self.search_entry)

        filters = Gtk.Box(spacing=6)
        self.category_dropdown = Gtk.DropDown.new_from_strings([ALL_CATEGORIES])
        self.category_dropdown.set_hexpand(True)
        self.category_dropdown.update_property([Gtk.AccessibleProperty.LABEL], ["Categoría"])
        self.category_dropdown.connect("notify::selected", lambda *_: self.refresh())
        filters.append(self.category_dropdown)
        self.favorites_toggle = Gtk.ToggleButton(icon_name="starred-symbolic", tooltip_text="Solo favoritas")
        self.favorites_toggle.connect("toggled", lambda *_: self.refresh())
        filters.append(self.favorites_toggle)
        column.append(filters)

        self.template_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.template_list.add_css_class("navigation-sidebar")
        self.template_list.connect("row-selected", self._on_row_selected)
        scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_child(self.template_list)
        column.append(scroller)
        return column

    def _build_detail_column(self) -> Gtk.Widget:
        self.detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16,
                              margin_start=24, margin_end=24, margin_top=20, margin_bottom=24)

        header = Gtk.Box(spacing=12)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
        self.title_label = label("", css=("title-2",), wrap=True)
        self.category_label = label("", css=("mks-pill",))
        self.category_label.set_halign(Gtk.Align.START)
        titles.append(self.title_label)
        titles.append(self.category_label)
        header.append(titles)
        self.favorite_button = Gtk.ToggleButton(icon_name="non-starred-symbolic", valign=Gtk.Align.START,
                                                tooltip_text="Añadir a favoritas")
        self.favorite_button.add_css_class("flat")
        self.favorite_button.connect("toggled", self._on_favorite_toggled)
        header.append(self.favorite_button)
        self.delete_button = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.START,
                                        tooltip_text="Eliminar plantilla personalizada")
        self.delete_button.add_css_class("flat")
        self.delete_button.connect("clicked", lambda *_: self._delete_custom())
        header.append(self.delete_button)
        self.detail.append(header)

        self.description_label = label("", wrap=True)
        self.detail.append(self.description_label)
        self.risk_box = Gtk.Box()
        self.detail.append(self.risk_box)

        script_group = Adw.PreferencesGroup(title="Script")
        self.name_row = Adw.EntryRow(title="Nombre del archivo")
        self.name_row.connect("changed", lambda *_: self._schedule_preview())
        script_group.add(self.name_row)
        self.description_row = Adw.EntryRow(title="Descripción (opcional)")
        self.description_row.connect("changed", lambda *_: self._schedule_preview())
        script_group.add(self.description_row)
        self.output_row = Adw.ActionRow(title="Se guardará en")
        self.output_row.set_subtitle_selectable(True)
        script_group.add(self.output_row)
        self.detail.append(script_group)

        self.form = TemplateForm(self._schedule_preview)
        self.detail.append(self.form)

        self.error_label = label("", css=("error",), wrap=True)
        self.error_label.set_visible(False)
        self.detail.append(self.error_label)

        actions = Gtk.Box(spacing=8)
        self.open_button = text_button("Abrir en Studio", self.open_in_studio, icon="document-edit-symbolic",
                                       css=("pill",))
        self.save_button = text_button("Generar y guardar", self.generate_and_save, icon="document-save-symbolic",
                                       css=("pill", "suggested-action"))
        actions.append(self.open_button)
        actions.append(self.save_button)
        self.detail.append(actions)

        self.detail.append(label("Vista previa", css=("heading",)))
        self.preview = CodeEditor(dark=Adw.StyleManager.get_default().get_dark(), read_only=True)
        self.preview.apply_preferences(self.services.prefs.preferences)
        self.preview.set_size_request(-1, 420)
        self.preview.add_css_class("mks-preview")
        self.detail.append(self.preview)

        clamp = Adw.Clamp(maximum_size=900, tightening_threshold=600)
        clamp.set_child(self.detail)
        scroller = Gtk.ScrolledWindow(hexpand=True, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_child(clamp)

        self.empty_page = Adw.StatusPage(icon_name="edit-find-symbolic", title="Sin resultados",
                                         description="Ninguna plantilla coincide con la búsqueda o los filtros.")
        self.detail_stack = Gtk.Stack()
        self.detail_stack.add_named(scroller, "detail")
        self.detail_stack.add_named(self.empty_page, "empty")
        return self.detail_stack

    # -- lista -------------------------------------------------------------

    def refresh(self) -> None:
        if self._loading:
            return
        self._loading = True
        registry = self.services.templates
        categories = [ALL_CATEGORIES, *registry.categories()]
        selected_category = self._selected_category()
        model = self.category_dropdown.get_model()
        current = [model.get_string(i) for i in range(model.get_n_items())]
        if current != categories:
            self.category_dropdown.set_model(Gtk.StringList.new(categories))
            self.category_dropdown.set_selected(categories.index(selected_category)
                                                if selected_category in categories else 0)
        self._loading = False

        only = set(self.services.state.favorite_templates) if self.favorites_toggle.get_active() else None
        category = self._selected_category()
        self._visible = registry.search(self.search_entry.get_text(),
                                        None if category == ALL_CATEGORIES else category, only)
        previous = self.template.id if self.template else None
        clear_container(self.template_list)
        for template in self._visible:
            self.template_list.append(self._make_row(template))
        if not self._visible:
            self.template = None
            self.detail_stack.set_visible_child_name("empty")
            return
        self.detail_stack.set_visible_child_name("detail")
        index = next((i for i, t in enumerate(self._visible) if t.id == previous), 0)
        self.template_list.select_row(self.template_list.get_row_at_index(index))

    def _selected_category(self) -> str:
        item = self.category_dropdown.get_selected_item()
        return item.get_string() if item is not None else ALL_CATEGORIES

    def _make_row(self, template: Template) -> Gtk.ListBoxRow:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=6, margin_bottom=6,
                      margin_start=6, margin_end=6)
        top = Gtk.Box(spacing=6)
        top.append(label(template.name, css=("heading",), ellipsize=True))
        if self.services.state.is_favorite(template.id):
            star = Gtk.Image(icon_name="starred-symbolic")
            star.add_css_class("accent")
            top.append(star)
        box.append(top)
        box.append(label(template.category, css=("dim-label", "caption")))
        row = Gtk.ListBoxRow()
        row.set_child(box)
        row.update_property([Gtk.AccessibleProperty.LABEL], [f"{template.name}, {template.category}"])
        return row

    def _on_row_selected(self, _list, row: Gtk.ListBoxRow | None) -> None:
        if row is None:
            return
        index = row.get_index()
        if 0 <= index < len(self._visible):
            self._show_template(self._visible[index])

    def select_template(self, template_id: str) -> None:
        self.search_entry.set_text("")
        self.favorites_toggle.set_active(False)
        self.category_dropdown.set_selected(0)
        self.refresh()
        for index, template in enumerate(self._visible):
            if template.id == template_id:
                self.template_list.select_row(self.template_list.get_row_at_index(index))
                break

    # -- detalle -------------------------------------------------------------

    def _show_template(self, template: Template) -> None:
        if self.template is not None and self.template.id == template.id:
            return
        self.template = template
        self._loading = True
        self.title_label.set_text(template.name)
        self.category_label.set_text(template.category)
        self.description_label.set_text(template.description)
        favorite = self.services.state.is_favorite(template.id)
        self.favorite_button.set_active(favorite)
        self._update_favorite_icon(favorite)
        self.delete_button.set_visible(template.is_custom)
        clear_container(self.risk_box)
        if not template.is_custom:
            self.risk_box.append(risk_banner(template))
        self.name_row.set_text(template.id.removeprefix("custom-").replace("-", "_") + ".sh")
        self.description_row.set_text("")
        self.form.set_template(template)
        self._loading = False
        self._schedule_preview()

    def _update_favorite_icon(self, favorite: bool) -> None:
        self.favorite_button.set_icon_name("starred-symbolic" if favorite else "non-starred-symbolic")
        self.favorite_button.set_tooltip_text("Quitar de favoritas" if favorite else "Añadir a favoritas")

    def _on_favorite_toggled(self, button: Gtk.ToggleButton) -> None:
        if self._loading or self.template is None:
            return
        self.services.state.set_favorite(self.template.id, button.get_active())
        self._update_favorite_icon(button.get_active())
        self.refresh()

    def _delete_custom(self) -> None:
        template = self.template
        if template is None or not template.is_custom:
            return

        def on_response(response: str) -> None:
            if response != "delete":
                return
            try:
                self.services.templates.delete_custom(template.id)
            except (KeyError, OSError) as exc:
                show_error(self, "No se pudo eliminar la plantilla", str(exc))
                return
            self.services.state.set_favorite(template.id, False)
            self.template = None
            self.window.templates_changed()
            self.window.toast(f"Plantilla «{template.name}» eliminada")

        ask(self, "¿Eliminar la plantilla?",
            f"Se eliminará «{template.name}» de la biblioteca. Los scripts ya generados no se modifican.",
            [("cancel", "Cancelar", ""), ("delete", "Eliminar", "destructive")], on_response)

    # -- generación ------------------------------------------------------------

    def _schedule_preview(self) -> None:
        if self._loading:
            return
        if self._preview_source:
            GLib.source_remove(self._preview_source)
        self._preview_source = GLib.timeout_add(120, self._update_preview)

    def _current(self) -> tuple[str | None, str | None, str | None]:
        """Devuelve ``(código, nombre normalizado, error)``."""
        if self.template is None:
            return None, None, "Elige una plantilla"
        try:
            name = normalize_script_name(self.name_row.get_text())
        except InvalidNameError as exc:
            self.name_row.add_css_class("error")
            return None, None, str(exc)
        self.name_row.remove_css_class("error")
        values = self.form.values()
        _, errors = self.template.validate(values)
        self.form.show_errors(errors)
        if errors:
            return None, name, "Corrige los parámetros marcados: " + "; ".join(errors.values())
        config = GenerationConfig.from_preferences(self.services.prefs.preferences, name, self.description_row.get_text())
        try:
            return generate_script(self.template, config, values), name, None
        except GenerationError as exc:
            return None, name, str(exc)

    def _update_preview(self) -> bool:
        self._preview_source = 0
        code, name, error = self._current()
        self.error_label.set_text(error or "")
        self.error_label.set_visible(bool(error))
        self.open_button.set_sensitive(code is not None)
        self.save_button.set_sensitive(code is not None)
        prefs = self.services.prefs.preferences
        self.output_row.set_subtitle(tilde_path(prefs.output_path() / name) if name else tilde_path(prefs.output_path()))
        if code is not None:
            self.preview.set_text(code)
        return GLib.SOURCE_REMOVE

    def open_in_studio(self) -> None:
        code, name, error = self._current()
        if code is None or name is None or self.template is None:
            show_error(self, "No se puede generar", error or "")
            return
        self.window.studio.load_generated(self.template, self.form.values(), name, self.description_row.get_text())
        self.window.show_section("studio")

    def generate_and_save(self) -> None:
        code, name, error = self._current()
        if code is None or name is None:
            show_error(self, "No se puede generar", error or "")
            return
        prefs = self.services.prefs.preferences
        try:
            directory = self.services.files.ensure_directory(prefs.output_path())
        except FileServiceError as exc:
            show_error(self, "No se pudo preparar la carpeta de salida", str(exc))
            return
        self._save(directory / name, code, overwrite=False)

    def _save(self, path: Path, code: str, overwrite: bool) -> None:
        prefs = self.services.prefs.preferences
        try:
            result = self.services.files.save(
                path, code, overwrite=overwrite,
                backup=self.services.backup_service() if overwrite else None,
                make_executable=prefs.executable_permissions,
            )
        except FileExistsConflict:
            if prefs.backup_before_overwrite:
                body = (f"Ya existe {tilde_path(path)}. Antes de sobrescribirlo se guardará una copia "
                        "de seguridad con la fecha y hora.")
            else:
                body = (f"Ya existe {tilde_path(path)}. Las copias de seguridad están desactivadas en "
                        "Preferencias: su contenido actual se perderá.")
            ask(self, f"¿Sobrescribir {path.name}?", body,
                [("cancel", "Cancelar", ""), ("overwrite", "Sobrescribir", "destructive")],
                lambda r: r == "overwrite" and self._save(path, code, overwrite=True))
            return
        except FileServiceError as exc:
            show_error(self, "No se pudo guardar", str(exc))
            return
        self.services.state.add_recent(result.path)
        message = f"Script guardado en {tilde_path(result.path)}"
        if result.backup_path:
            message += f" · copia: {result.backup_path.name}"
        self.window.toast(message)
        if prefs.open_after_generate:
            self.window.studio.open_path(result.path)
