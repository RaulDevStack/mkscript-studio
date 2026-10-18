"""Template parameter form and risk summary."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from mkscript_studio.generator.models import ParamType, Template, TemplateParam  # noqa: E402
from mkscript_studio.ui.common import choose_folder, tilde_path  # noqa: E402


class TemplateForm(Gtk.Box):
    """Build the right control for each parameter type."""

    __gtype_name__ = "MksTemplateForm"

    def __init__(self, on_changed: Callable[[], None]) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self._on_changed = on_changed
        self.template: Template | None = None
        self._getters: dict[str, Callable[[], Any]] = {}
        self._rows: dict[str, Adw.PreferencesRow] = {}
        self._group: Adw.PreferencesGroup | None = None

    def set_template(self, template: Template | None, values: dict[str, Any] | None = None) -> None:
        if self._group is not None:
            self.remove(self._group)
            self._group = None
        self.template = template
        self._getters = {}
        self._rows = {}
        if template is None or not template.params:
            return
        values = {**template.default_values(), **(values or {})}
        group = Adw.PreferencesGroup(title="Parameters")
        for param in template.params:
            row = self._build_row(param, values.get(param.key, param.default))
            self._rows[param.key] = row
            group.add(row)
        self._group = group
        self.append(group)

    def _build_row(self, param: TemplateParam, value: Any) -> Adw.PreferencesRow:
        changed = lambda *_: self._on_changed()  # noqa: E731

        if param.type is ParamType.BOOL:
            switch_row = Adw.SwitchRow(title=param.label, subtitle=param.description, active=bool(value))
            switch_row.connect("notify::active", changed)
            self._getters[param.key] = switch_row.get_active
            return switch_row

        if param.type is ParamType.INT:
            spin_row = Adw.SpinRow.new_with_range(param.minimum if param.minimum is not None else 0,
                                                  param.maximum if param.maximum is not None else 1_000_000, 1)
            spin_row.set_title(param.label)
            spin_row.set_subtitle(param.description)
            spin_row.set_value(int(value))
            spin_row.connect("notify::value", changed)
            self._getters[param.key] = lambda: int(spin_row.get_value())
            return spin_row

        if param.type is ParamType.CHOICE:
            keys = [key for key, _ in param.choices]
            model = Gtk.StringList.new([text for _, text in param.choices])
            combo_row = Adw.ComboRow(title=param.label, subtitle=param.description, model=model)
            combo_row.set_selected(keys.index(value) if value in keys else 0)
            combo_row.connect("notify::selected", changed)
            self._getters[param.key] = lambda: keys[combo_row.get_selected()]
            return combo_row

        entry_row = Adw.EntryRow(title=param.label)
        entry_row.set_text(", ".join(value) if isinstance(value, list) else str(value))
        if param.description:
            entry_row.set_tooltip_text(param.description)
        entry_row.connect("changed", changed)
        self._getters[param.key] = entry_row.get_text
        if param.type is ParamType.PATH:
            button = Gtk.Button(icon_name="folder-open-symbolic", valign=Gtk.Align.CENTER,
                                tooltip_text="Choose folder")
            button.add_css_class("flat")
            button.connect("clicked", lambda _b: self._pick_folder(entry_row))
            entry_row.add_suffix(button)
        return entry_row

    def _pick_folder(self, row: Adw.EntryRow) -> None:
        current = Path(row.get_text().replace("~", str(Path.home()), 1)) if row.get_text() else Path.home()
        choose_folder(self.get_root(), lambda path: row.set_text(tilde_path(path)), current)

    def values(self) -> dict[str, Any]:
        return {key: getter() for key, getter in self._getters.items()}

    def show_errors(self, errors: dict[str, str]) -> None:
        for key, row in self._rows.items():
            message = errors.get(key)
            if message:
                row.add_css_class("error")
                row.set_tooltip_text(message)
            else:
                row.remove_css_class("error")
                param = next(p for p in self.template.params if p.key == key) if self.template else None
                row.set_tooltip_text(param.description if param and param.description else None)


def risk_banner(template: Template) -> Gtk.Widget:
    """Block summarizing the risk level and its notes."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    box.add_css_class("mks-risk")
    box.add_css_class(f"risk-{template.risk.level.value}")
    title = Gtk.Label(label=template.risk.level.label, xalign=0)
    title.add_css_class("heading")
    box.append(title)
    notes = template.risk.notes or ("Review the code before running it.",)
    for note in notes:
        item = Gtk.Label(label=f"• {note}", xalign=0, wrap=True)
        item.add_css_class("caption")
        box.append(item)
    return box
