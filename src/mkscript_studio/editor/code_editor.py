"""Code editor based on GtkSourceView 5.

Includes Bash highlighting, line numbers, find/replace, diagnostic marks and
the Tab completion described in ``completion.resolve_tab``.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("GtkSource", "5")
from gi.repository import Gdk, GLib, GObject, Gtk, GtkSource, Pango  # noqa: E402

from mkscript_studio.editor.completion import (  # noqa: E402
    Candidate,
    CompletionEngine,
    CompletionKind,
    TabActionKind,
    resolve_tab,
    scan_path_commands,
)
from mkscript_studio.paths import resources_dir  # noqa: E402
from mkscript_studio.preferences.models import Preferences  # noqa: E402
from mkscript_studio.services.validation_service import Diagnostic  # noqa: E402

_scheme_path_added = False


def style_scheme(dark: bool) -> GtkSource.StyleScheme | None:
    global _scheme_path_added
    manager = GtkSource.StyleSchemeManager.get_default()
    if not _scheme_path_added:
        manager.append_search_path(str(resources_dir() / "styles"))
        _scheme_path_added = True
    return manager.get_scheme("mkscript-dark" if dark else "mkscript-light") or manager.get_scheme(
        "Adwaita-dark" if dark else "Adwaita"
    )


def new_source_buffer(dark: bool = True) -> GtkSource.Buffer:
    buffer = GtkSource.Buffer()
    language = GtkSource.LanguageManager.get_default().get_language("sh")
    if language is not None:
        buffer.set_language(language)
    buffer.set_highlight_syntax(True)
    buffer.set_highlight_matching_brackets(True)
    scheme = style_scheme(dark)
    if scheme is not None:
        buffer.set_style_scheme(scheme)
    return buffer


def iter_at_line(buffer: Gtk.TextBuffer, line: int, column: int = 0) -> Gtk.TextIter:
    """``get_iter_at_line*`` returns ``(bool, iter)`` in GTK 4."""
    line = max(0, min(line, buffer.get_line_count() - 1))
    result = buffer.get_iter_at_line(line)
    it = result[1] if isinstance(result, tuple) else result
    if column > 0:
        it.forward_chars(min(column, max(0, it.get_chars_in_line() - 1)))
    return it


_KIND_BADGE = {
    CompletionKind.FUNCTION: ("fn", "kind-function"),
    CompletionKind.KEYWORD: ("kw", "kind-keyword"),
    CompletionKind.BUILTIN: ("bi", "kind-builtin"),
    CompletionKind.COMMAND: ("cmd", "kind-command"),
    CompletionKind.SYSTEM_COMMAND: ("sys", "kind-command"),
    CompletionKind.VARIABLE: ("$", "kind-variable"),
    CompletionKind.PATH: ("/", "kind-path"),
}


class CompletionPopup(Gtk.Popover):
    """Suggestion list that does not steal focus from the editor."""

    __gtype_name__ = "MksCompletionPopup"

    def __init__(self, on_activate: Callable[[Candidate], None]) -> None:
        super().__init__(autohide=False, has_arrow=False, position=Gtk.PositionType.BOTTOM)
        self.add_css_class("mks-completion")
        self.set_can_focus(False)
        self._on_activate = on_activate
        self.candidates: list[Candidate] = []
        self.explicit = False

        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE, can_focus=False)
        self.listbox.update_property([Gtk.AccessibleProperty.LABEL], ["Autocompletion suggestions"])
        self.listbox.connect("row-activated", self._row_activated)
        self.scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            propagate_natural_height=True,
            propagate_natural_width=True,
            max_content_height=260,
            min_content_width=340,
        )
        self.scroller.set_child(self.listbox)
        self.set_child(self.scroller)

    def show_candidates(self, candidates: list[Candidate], rect: Gdk.Rectangle) -> None:
        self.candidates = candidates
        child = self.listbox.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self.listbox.remove(child)
            child = following
        for candidate in candidates:
            self.listbox.append(self._make_row(candidate))
        self.set_pointing_to(rect)
        if not self.get_visible():
            self.popup()
        self.present()
        self.select(0)

    @staticmethod
    def _make_row(candidate: Candidate) -> Gtk.ListBoxRow:
        badge_text, badge_css = _KIND_BADGE[candidate.kind]
        badge = Gtk.Label(label=badge_text, width_chars=3)
        badge.add_css_class("kind-badge")
        badge.add_css_class(badge_css)
        name = Gtk.Label(label=candidate.label, xalign=0, hexpand=True)
        name.add_css_class("monospace")
        detail = Gtk.Label(label=candidate.detail or candidate.kind.value, xalign=1,
                           ellipsize=Pango.EllipsizeMode.END, max_width_chars=28)
        detail.add_css_class("dim-label")
        detail.add_css_class("caption")
        box = Gtk.Box(spacing=8, margin_start=4, margin_end=4, margin_top=2, margin_bottom=2)
        box.append(badge)
        box.append(name)
        box.append(detail)
        row = Gtk.ListBoxRow(can_focus=False)
        row.set_child(box)
        row.update_property([Gtk.AccessibleProperty.LABEL], [f"{candidate.label}, {candidate.kind.value}"])
        return row

    def selected_index(self) -> int:
        row = self.listbox.get_selected_row()
        return row.get_index() if row is not None else 0

    def select(self, index: int) -> None:
        if not self.candidates:
            return
        index %= len(self.candidates)
        row = self.listbox.get_row_at_index(index)
        if row is None:
            return
        self.listbox.select_row(row)
        ok, bounds = row.compute_bounds(self.listbox)
        if ok:
            adjustment = self.scroller.get_vadjustment()
            top, bottom = bounds.get_y(), bounds.get_y() + bounds.get_height()
            if top < adjustment.get_value():
                adjustment.set_value(top)
            elif bottom > adjustment.get_value() + adjustment.get_page_size():
                adjustment.set_value(bottom - adjustment.get_page_size())

    def move(self, delta: int) -> None:
        self.select(self.selected_index() + delta)

    def current(self) -> Candidate | None:
        return self.candidates[self.selected_index()] if self.candidates else None

    def dismiss(self) -> None:
        self.explicit = False
        if self.get_visible():
            self.popdown()

    def _row_activated(self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        self._on_activate(self.candidates[row.get_index()])


class SearchBar(Gtk.Revealer):
    """Find and replace with GtkSource.SearchContext."""

    __gtype_name__ = "MksSearchBar"

    def __init__(self, buffer: GtkSource.Buffer, view: GtkSource.View) -> None:
        super().__init__(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.buffer = buffer
        self.view = view
        self.settings = GtkSource.SearchSettings(wrap_around=True)
        self.context = GtkSource.SearchContext.new(buffer, self.settings)
        self.context.set_highlight(True)
        self.context.connect("notify::occurrences-count", lambda *_: self._update_count())

        self.search_entry = Gtk.SearchEntry(placeholder_text="Find", hexpand=True)
        self.search_entry.update_property([Gtk.AccessibleProperty.LABEL], ["Find"])
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.search_entry.connect("activate", lambda *_: self.find(forward=True))
        self.search_entry.connect("next-match", lambda *_: self.find(forward=True))
        self.search_entry.connect("previous-match", lambda *_: self.find(forward=False))
        self.search_entry.connect("stop-search", lambda *_: self.close())
        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self._on_entry_key)
        self.search_entry.add_controller(key)

        self.case_button = Gtk.ToggleButton(label="Aa", tooltip_text="Match case")
        self.case_button.connect("toggled", lambda b: self._set_option("case_sensitive", b.get_active()))
        self.regex_button = Gtk.ToggleButton(label=".*", tooltip_text="Regular expression")
        self.regex_button.connect("toggled", lambda b: self._set_option("regex_enabled", b.get_active()))
        self.word_button = Gtk.ToggleButton(label="‹w›", tooltip_text="Whole word")
        self.word_button.connect("toggled", lambda b: self._set_option("at_word_boundaries", b.get_active()))

        self.count_label = Gtk.Label(width_chars=12, xalign=0.5)
        self.count_label.add_css_class("dim-label")
        self.count_label.add_css_class("caption")

        prev_button = Gtk.Button(icon_name="go-up-symbolic", tooltip_text="Previous (Shift+Enter)")
        prev_button.connect("clicked", lambda *_: self.find(forward=False))
        next_button = Gtk.Button(icon_name="go-down-symbolic", tooltip_text="Next (Enter)")
        next_button.connect("clicked", lambda *_: self.find(forward=True))
        self.replace_toggle = Gtk.ToggleButton(icon_name="edit-find-replace-symbolic", tooltip_text="Replace (Ctrl+H)")
        self.replace_toggle.connect("toggled", lambda b: self.replace_row.set_visible(b.get_active()))
        close_button = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="Close (Esc)")
        close_button.add_css_class("flat")
        close_button.connect("clicked", lambda *_: self.close())

        options = Gtk.Box(css_classes=["linked"])
        for widget in (self.case_button, self.word_button, self.regex_button):
            options.append(widget)
        nav = Gtk.Box(css_classes=["linked"])
        nav.append(prev_button)
        nav.append(next_button)

        search_row = Gtk.Box(spacing=6)
        for widget in (self.search_entry, self.count_label, options, nav, self.replace_toggle, close_button):
            search_row.append(widget)

        self.replace_entry = Gtk.Entry(placeholder_text="Replace with", hexpand=True)
        self.replace_entry.update_property([Gtk.AccessibleProperty.LABEL], ["Replace with"])
        self.replace_entry.connect("activate", lambda *_: self.replace_one())
        replace_button = Gtk.Button(label="Replace")
        replace_button.connect("clicked", lambda *_: self.replace_one())
        replace_all_button = Gtk.Button(label="Replace all")
        replace_all_button.connect("clicked", lambda *_: self.replace_all())
        self.replace_row = Gtk.Box(spacing=6, visible=False)
        for widget in (self.replace_entry, replace_button, replace_all_button):
            self.replace_row.append(widget)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                      margin_start=8, margin_end=8, margin_top=6, margin_bottom=6)
        box.add_css_class("mks-searchbar")
        box.append(search_row)
        box.append(self.replace_row)
        self.set_child(box)

    def open(self, replace: bool = False) -> None:
        bounds = self.buffer.get_selection_bounds()
        if bounds:
            start, end = bounds
            if start.get_line() == end.get_line():
                self.search_entry.set_text(self.buffer.get_text(start, end, False))
        self.replace_toggle.set_active(replace or self.replace_toggle.get_active())
        self.set_reveal_child(True)
        self.context.set_highlight(True)
        self.search_entry.grab_focus()
        self.search_entry.select_region(0, -1)

    def close(self) -> None:
        self.set_reveal_child(False)
        self.context.set_highlight(False)
        self.view.grab_focus()

    def _set_option(self, name: str, value: bool) -> None:
        self.settings.set_property(name.replace("_", "-"), value)
        self._update_count()

    def _on_entry_key(self, _ctrl, keyval: int, _code: int, state: Gdk.ModifierType) -> bool:
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and state & Gdk.ModifierType.SHIFT_MASK:
            self.find(forward=False)
            return True
        return False

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        text = entry.get_text()
        self.settings.set_search_text(text or None)
        if text:
            self.find(forward=True, from_selection_start=True)
        self._update_count()

    def _update_count(self) -> None:
        if not self.settings.get_search_text():
            self.count_label.set_text("")
            return
        error = self.context.get_regex_error()
        if error is not None:
            self.count_label.set_text("Invalid regex")
            return
        total = self.context.get_occurrences_count()
        if total < 0:
            return  # still being computed
        bounds = self.buffer.get_selection_bounds()
        position = self.context.get_occurrence_position(*bounds) if bounds else -1
        if total == 0:
            self.count_label.set_text("No results")
        elif position > 0:
            self.count_label.set_text(f"{position} of {total}")
        else:
            self.count_label.set_text(f"{total} results")

    def find(self, forward: bool, from_selection_start: bool = False) -> bool:
        if not self.settings.get_search_text():
            return False
        bounds = self.buffer.get_selection_bounds()
        if bounds:
            anchor = bounds[0] if (from_selection_start or not forward) else bounds[1]
        else:
            anchor = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        result = self.context.forward(anchor) if forward else self.context.backward(anchor)
        found, start, end = result[0], result[1], result[2]
        if found:
            self.buffer.select_range(start, end)
            self.view.scroll_to_mark(self.buffer.get_insert(), 0.2, False, 0.0, 0.0)
        self._update_count()
        return found

    def replace_one(self) -> None:
        bounds = self.buffer.get_selection_bounds()
        if not bounds or self.context.get_occurrence_position(*bounds) <= 0:
            self.find(forward=True)
            return
        try:
            self.context.replace(bounds[0], bounds[1], self.replace_entry.get_text(), -1)
        except GLib.Error as exc:
            self.count_label.set_text(exc.message)
            return
        self.find(forward=True)

    def replace_all(self) -> None:
        if not self.settings.get_search_text():
            return
        try:
            count = self.context.replace_all(self.replace_entry.get_text(), -1)
        except GLib.Error as exc:
            self.count_label.set_text(exc.message)
            return
        self.count_label.set_text(f"{count} replaced")


class CodeEditor(Gtk.Box):
    """Complete editing widget: search bar + code view."""

    __gtype_name__ = "MksCodeEditor"
    __gsignals__ = {
        "cursor-moved": (GObject.SignalFlags.RUN_FIRST, None, (int, int)),
        "modified-changed": (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
    }

    def __init__(self, dark: bool = True, read_only: bool = False) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("mks-editor")
        self.buffer = new_source_buffer(dark)
        self.view = GtkSource.View.new_with_buffer(self.buffer)
        self.view.set_monospace(True)
        self.view.set_auto_indent(True)
        self.view.set_indent_on_tab(True)
        self.view.set_smart_backspace(True)
        self.view.set_smart_home_end(GtkSource.SmartHomeEndType.BEFORE)
        self.view.set_show_line_marks(True)
        self.view.set_top_margin(8)
        self.view.set_bottom_margin(64)
        self.view.set_left_margin(6)
        self.view.set_editable(not read_only)
        self.view.set_cursor_visible(not read_only)
        self.view.update_property([Gtk.AccessibleProperty.LABEL], ["Bash code editor"])
        self.read_only = read_only

        self.engine = CompletionEngine()
        self.completion_enabled = not read_only
        self.complete_while_typing = True
        self.min_chars = 2
        self._suppress_completion = False

        self._create_diagnostic_marks()

        scroller = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        scroller.set_child(self.view)

        self.search = SearchBar(self.buffer, self.view)
        self.append(self.search)
        self.append(scroller)

        self.popup = CompletionPopup(self._accept_candidate)
        self.popup.set_parent(self.view)

        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key_pressed)
        self.view.add_controller(keys)

        click = Gtk.GestureClick()
        click.connect("pressed", lambda *_: self.popup.dismiss())
        self.view.add_controller(click)
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", lambda *_: self.popup.dismiss())
        self.view.add_controller(focus)

        self.buffer.connect_after("insert-text", self._on_insert_text)
        self.buffer.connect_after("delete-range", self._on_delete_range)
        self.buffer.connect("notify::cursor-position", self._on_cursor_moved)
        self.buffer.connect("modified-changed", lambda b: self.emit("modified-changed", b.get_modified()))
        self.connect("destroy", lambda *_: self.popup.unparent())

    # -- text ----------------------------------------------------------------

    def get_text(self) -> str:
        start, end = self.buffer.get_bounds()
        return self.buffer.get_text(start, end, True)

    def set_text(self, text: str, modified: bool = False) -> None:
        """Replace the content without undo history (used when opening a file)."""
        self._suppress_completion = True
        self.popup.dismiss()
        self.buffer.begin_irreversible_action()
        self.buffer.set_text(text)
        self.buffer.end_irreversible_action()
        self.buffer.place_cursor(self.buffer.get_start_iter())
        self.buffer.set_modified(modified)
        self._suppress_completion = False
        self.clear_diagnostics()

    def replace_text(self, text: str) -> None:
        """Replace the content as an undoable action."""
        self._suppress_completion = True
        self.buffer.begin_user_action()
        start, end = self.buffer.get_bounds()
        self.buffer.delete(start, end)
        self.buffer.insert(self.buffer.get_start_iter(), text)
        self.buffer.end_user_action()
        self.buffer.place_cursor(self.buffer.get_start_iter())
        self._suppress_completion = False
        self.clear_diagnostics()

    def is_modified(self) -> bool:
        return self.buffer.get_modified()

    def set_modified(self, value: bool) -> None:
        self.buffer.set_modified(value)

    def undo(self) -> None:
        if self.buffer.get_can_undo():
            self.buffer.undo()

    def redo(self) -> None:
        if self.buffer.get_can_redo():
            self.buffer.redo()

    def cursor_position(self) -> tuple[int, int]:
        it = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        return it.get_line(), it.get_line_offset()

    def goto_line(self, line: int, column: int = 0) -> None:
        """``line`` and ``column`` start at 1, as in diagnostic messages."""
        it = iter_at_line(self.buffer, line - 1, max(0, column - 1))
        self.buffer.place_cursor(it)
        self.view.scroll_to_mark(self.buffer.get_insert(), 0.25, True, 0.0, 0.4)
        self.view.grab_focus()

    def focus(self) -> None:
        self.view.grab_focus()

    # -- preferences ----------------------------------------------------------

    def apply_preferences(self, prefs: Preferences) -> None:
        self.view.set_tab_width(prefs.editor_tab_width)
        self.view.set_indent_width(-1)
        self.view.set_insert_spaces_instead_of_tabs(prefs.editor_insert_spaces)
        self.view.set_show_line_numbers(prefs.editor_show_line_numbers)
        self.view.set_highlight_current_line(prefs.editor_highlight_current_line)
        self.view.set_show_right_margin(prefs.editor_show_right_margin)
        self.view.set_right_margin_position(100)
        self.view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR if prefs.editor_wrap_text else Gtk.WrapMode.NONE)
        self.completion_enabled = prefs.completion_enabled and not self.read_only
        self.complete_while_typing = prefs.completion_while_typing
        self.min_chars = prefs.completion_min_chars
        self.engine.system_commands = scan_path_commands if prefs.completion_system_commands else None
        self.engine._system_cache = None
        if not self.completion_enabled:
            self.popup.dismiss()

    def set_dark(self, dark: bool) -> None:
        scheme = style_scheme(dark)
        if scheme is not None:
            self.buffer.set_style_scheme(scheme)

    def set_base_directory(self, directory: Path | None) -> None:
        self.engine.base_directory = directory

    # -- diagnostics ------------------------------------------------------

    def _create_diagnostic_marks(self) -> None:
        self._diagnostic_messages: dict[int, list[str]] = {}
        for category, icon, priority in (("error", "dialog-error-symbolic", 20),
                                         ("warning", "dialog-warning-symbolic", 10),
                                         ("info", "dialog-information-symbolic", 5)):
            attrs = GtkSource.MarkAttributes()
            attrs.set_icon_name(icon)
            attrs.connect("query-tooltip-text", self._mark_tooltip)
            self.view.set_mark_attributes(category, attrs, priority)
        self.buffer.create_tag("diag-error", underline=Pango.Underline.ERROR)
        warning_tag = self.buffer.create_tag("diag-warning", underline=Pango.Underline.ERROR)
        rgba = Gdk.RGBA()
        rgba.parse("#e5a50a")
        warning_tag.set_property("underline-rgba", rgba)

    def _mark_tooltip(self, _attrs, mark) -> str:
        line = self.buffer.get_iter_at_mark(mark).get_line() + 1
        return "\n".join(self._diagnostic_messages.get(line, []))

    def clear_diagnostics(self) -> None:
        start, end = self.buffer.get_bounds()
        for category in ("error", "warning", "info"):
            self.buffer.remove_source_marks(start, end, category)
        self.buffer.remove_tag_by_name("diag-error", start, end)
        self.buffer.remove_tag_by_name("diag-warning", start, end)
        self._diagnostic_messages = {}

    def add_diagnostics(self, diagnostics: list[Diagnostic]) -> None:
        for diagnostic in diagnostics:
            if not diagnostic.line or diagnostic.line > self.buffer.get_line_count():
                continue
            category = {"error": "error", "warning": "warning"}.get(diagnostic.severity, "info")
            start = iter_at_line(self.buffer, diagnostic.line - 1)
            end = start.copy()
            if not end.ends_line():
                end.forward_to_line_end()
            self.buffer.create_source_mark(None, category, start)
            if category in ("error", "warning"):
                self.buffer.apply_tag_by_name(f"diag-{category}", start, end)
            code = f"{diagnostic.code}: " if diagnostic.code else ""
            self._diagnostic_messages.setdefault(diagnostic.line, []).append(f"{code}{diagnostic.message}")

    # -- autocompletion ---------------------------------------------------

    def _cursor_offset(self) -> int:
        return self.buffer.get_iter_at_mark(self.buffer.get_insert()).get_offset()

    def _on_key_pressed(self, _ctrl, keyval: int, _code: int, state: Gdk.ModifierType) -> bool:
        if not self.completion_enabled:
            return False
        modifiers = state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SHIFT_MASK)
        popup_open = self.popup.get_visible()

        if keyval == Gdk.KEY_space and modifiers == Gdk.ModifierType.CONTROL_MASK:
            self.trigger_completion()
            return True

        if popup_open:
            if keyval in (Gdk.KEY_Escape,):
                self.popup.dismiss()
                return True
            if keyval in (Gdk.KEY_Down, Gdk.KEY_KP_Down) and not modifiers:
                self.popup.move(1)
                return True
            if keyval in (Gdk.KEY_Up, Gdk.KEY_KP_Up) and not modifiers:
                self.popup.move(-1)
                return True
            if keyval in (Gdk.KEY_Page_Down, Gdk.KEY_Page_Up) and not modifiers:
                self.popup.move(8 if keyval == Gdk.KEY_Page_Down else -8)
                return True
            if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_Tab) and not modifiers:
                candidate = self.popup.current()
                if candidate is not None:
                    self._accept_candidate(candidate)
                    return True
            return False

        if keyval == Gdk.KEY_Tab and not modifiers and not self.buffer.get_has_selection():
            return self._handle_tab()
        return False

    def _handle_tab(self) -> bool:
        """Return ``False`` if Tab should behave as normal indentation."""
        action = resolve_tab(self.engine, self.get_text(), self._cursor_offset())
        if action.kind is TabActionKind.INDENT:
            return False
        self._replace_range(action.start, action.end, action.text)
        if action.kind is TabActionKind.SHOW:
            self.popup.explicit = True
            self.refresh_completion(explicit=True)
        return True

    def trigger_completion(self) -> None:
        self.popup.explicit = True
        self.refresh_completion(explicit=True)

    def refresh_completion(self, explicit: bool = False) -> None:
        if not self.completion_enabled:
            return
        explicit = explicit or self.popup.explicit
        offset = self._cursor_offset()
        context, candidates = self.engine.complete(self.get_text(), offset, explicit=explicit, min_chars=self.min_chars)
        if not context.prefix or not candidates or (len(candidates) == 1 and candidates[0].insert_text == context.prefix):
            self.popup.dismiss()
            return
        start = self.buffer.get_iter_at_offset(context.start)
        location = self.view.get_iter_location(start)
        x, y = self.view.buffer_to_window_coords(Gtk.TextWindowType.WIDGET, location.x, location.y)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = x, y, 1, max(location.height, 1)
        self.popup.show_candidates(candidates, rect)

    def _accept_candidate(self, candidate: Candidate) -> None:
        offset = self._cursor_offset()
        context = self.engine.context_at(self.get_text(), offset)
        self.popup.dismiss()
        self._replace_range(context.start, offset, candidate.insert_text)
        self.view.grab_focus()
        if candidate.kind is CompletionKind.PATH and candidate.insert_text.endswith("/"):
            # After entering a folder, offer its contents right away.
            GLib.idle_add(self._idle_refresh, True)

    def _replace_range(self, start_offset: int, end_offset: int, text: str) -> None:
        self._suppress_completion = True
        self.buffer.begin_user_action()
        start = self.buffer.get_iter_at_offset(start_offset)
        end = self.buffer.get_iter_at_offset(end_offset)
        self.buffer.delete(start, end)
        self.buffer.insert(self.buffer.get_iter_at_offset(start_offset), text)
        self.buffer.end_user_action()
        self._suppress_completion = False

    def _idle_refresh(self, explicit: bool) -> bool:
        self.refresh_completion(explicit=explicit)
        return GLib.SOURCE_REMOVE

    def _on_insert_text(self, _buffer, _location, text: str, _length: int) -> None:
        if self._suppress_completion or not self.completion_enabled:
            return
        if self.popup.get_visible():
            GLib.idle_add(self._idle_refresh, False)
        elif self.complete_while_typing and len(text) == 1 and (text.isalnum() or text in "_-$/{."):
            GLib.idle_add(self._idle_refresh, False)

    def _on_delete_range(self, *_args) -> None:
        if not self._suppress_completion and self.popup.get_visible():
            GLib.idle_add(self._idle_refresh, False)

    def _on_cursor_moved(self, *_args) -> None:
        if self.popup.get_visible() and not self._suppress_completion:
            GLib.idle_add(self._idle_refresh, False)
        line, column = self.cursor_position()
        self.emit("cursor-moved", line + 1, column + 1)
