"""Studio: editor, files panel, generation panel and bottom panel."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, GObject, Gtk, Pango  # noqa: E402

from mkscript_studio.editor.code_editor import CodeEditor  # noqa: E402
from mkscript_studio.generator.engine import GenerationError, generate_script  # noqa: E402
from mkscript_studio.generator.models import GenerationConfig, Template  # noqa: E402
from mkscript_studio.services.execution_service import (  # noqa: E402
    ExecutionError,
    ExecutionSession,
    analyze_risks,
    build_plan,
)
from mkscript_studio.services.file_service import (  # noqa: E402
    FileServiceError,
    InvalidNameError,
    normalize_script_name,
)
from mkscript_studio.services.process import CancellableProcess  # noqa: E402
from mkscript_studio.services.validation_service import CheckResult, CheckStatus, Diagnostic  # noqa: E402
from mkscript_studio.ui.common import (  # noqa: E402
    ask,
    choose_file_to_open,
    choose_file_to_save,
    clear_container,
    icon_button,
    idle,
    label,
    open_in_file_manager,
    run_async,
    show_error,
    text_button,
    tilde_path,
)
from mkscript_studio.ui.template_form import TemplateForm, risk_banner  # noqa: E402

if TYPE_CHECKING:
    from mkscript_studio.context import AppServices
    from mkscript_studio.window import MainWindow

UNTITLED = "untitled.sh"
_SEVERITY_ICON = {
    "error": "dialog-error-symbolic",
    "warning": "dialog-warning-symbolic",
    "info": "dialog-information-symbolic",
    "style": "dialog-information-symbolic",
}


@dataclass
class Document:
    path: Path | None = None
    script_name: str = UNTITLED
    description: str = ""
    template_id: str | None = None
    values: dict[str, Any] = field(default_factory=dict)
    #: Modification time at open/save, to detect external changes.
    mtime: float | None = None
    #: The original file has already been backed up in this session.
    backup_done: bool = False
    #: Suggested folder for the first save (e.g. a project folder).
    save_dir: Path | None = None

    @property
    def title(self) -> str:
        return self.path.name if self.path else self.script_name


class StudioView(Gtk.Box):
    __gtype_name__ = "MksStudioView"

    def __init__(self, window: MainWindow, services: AppServices) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.window = window
        self.services = services
        self.document = Document()
        self._check_jobs: dict[str, CancellableProcess] = {}
        self._diagnostics: dict[str, list[Diagnostic]] = {}
        self._diagnostics_stale = False
        self._session: ExecutionSession | None = None
        self._preview_source: int = 0
        self._loading_panel = False
        self._last_summary = ""
        self._file_paths: list[Path] = []
        self._problem_items: list[Diagnostic] = []

        dark = Adw.StyleManager.get_default().get_dark()
        self.editor = CodeEditor(dark=dark)
        self.editor.connect("cursor-moved", self._on_cursor_moved)
        self.editor.connect("modified-changed", lambda *_: self._update_state())
        self.editor.buffer.connect("changed", self._on_text_changed)

        self.append(self._build_toolbar())
        self.append(Gtk.Separator())
        self.append(self._build_body())
        self.append(Gtk.Separator())
        self.append(self._build_statusbar())

        services.prefs.subscribe(lambda prefs, _changed: self._apply_preferences())
        Adw.StyleManager.get_default().connect("notify::dark", self._on_dark_changed)
        self._apply_preferences()
        self._set_document(Document(), self._starter_code())

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_toolbar(self) -> Gtk.Widget:
        bar = Gtk.Box(spacing=4, margin_start=8, margin_end=8, margin_top=6, margin_bottom=6)
        bar.add_css_class("mks-toolbar")

        files = Gtk.Box(css_classes=["linked"])
        files.append(icon_button("document-new-symbolic", "New (Ctrl+N)", self.new_document, css=()))
        files.append(icon_button("document-open-symbolic", "Open (Ctrl+O)", self.open_dialog, css=()))
        files.append(icon_button("document-save-symbolic", "Save (Ctrl+S)", self.save, css=()))
        files.append(icon_button("document-save-as-symbolic", "Save as (Ctrl+Shift+S)", self.save_as, css=()))
        bar.append(files)

        edit = Gtk.Box(css_classes=["linked"])
        edit.append(icon_button("edit-undo-symbolic", "Undo (Ctrl+Z)", self.editor.undo, css=()))
        edit.append(icon_button("edit-redo-symbolic", "Redo (Ctrl+Shift+Z)", self.editor.redo, css=()))
        bar.append(edit)

        search = Gtk.Box(css_classes=["linked"])
        search.append(icon_button("edit-find-symbolic", "Find (Ctrl+F)", self.show_search, css=()))
        search.append(icon_button("edit-find-replace-symbolic", "Replace (Ctrl+H)",
                                  lambda: self.show_search(replace=True), css=()))
        bar.append(search)

        bar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL, margin_start=4, margin_end=4))
        self.check_button = text_button("Check syntax", self.check_syntax, icon="emblem-ok-symbolic",
                                        tooltip="Parse the code with bash -n without running it (F7)")
        bar.append(self.check_button)
        self.shellcheck_button = text_button("ShellCheck", self.run_shellcheck,
                                             tooltip="Additional static analysis (Shift+F7)")
        bar.append(self.shellcheck_button)

        spacer = Gtk.Box(hexpand=True)
        bar.append(spacer)

        self.run_button = text_button("Run…", self.run_script, icon="media-playback-start-symbolic",
                                      tooltip="Run the saved file after confirming (F5)")
        bar.append(self.run_button)
        self.stop_button = icon_button("media-playback-stop-symbolic", "Stop execution", self.stop_script,
                                       css=("destructive-action",))
        self.stop_button.set_visible(False)
        bar.append(self.stop_button)

        bar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL, margin_start=4, margin_end=4))
        self.files_toggle = self._panel_toggle("sidebar-show-symbolic", "Files panel")
        self.bottom_toggle = self._panel_toggle("utilities-terminal-symbolic", "Problems and output panel")
        self.generation_toggle = self._panel_toggle("sidebar-show-right-symbolic", "Generation panel")
        for toggle in (self.files_toggle, self.bottom_toggle, self.generation_toggle):
            bar.append(toggle)
        return bar

    @staticmethod
    def _panel_toggle(icon: str, tooltip: str) -> Gtk.ToggleButton:
        toggle = Gtk.ToggleButton(icon_name=icon, tooltip_text=tooltip, active=True)
        toggle.add_css_class("flat")
        toggle.update_property([Gtk.AccessibleProperty.LABEL], [tooltip])
        return toggle

    def _build_body(self) -> Gtk.Widget:
        self.files_panel = self._build_files_panel()
        self.bottom_panel = self._build_bottom_panel()
        self.generation_panel = self._build_generation_panel()

        flags = GObject.BindingFlags.SYNC_CREATE | GObject.BindingFlags.BIDIRECTIONAL
        self.files_toggle.bind_property("active", self.files_panel, "visible", flags)
        self.bottom_toggle.bind_property("active", self.bottom_panel, "visible", flags)
        self.generation_toggle.bind_property("active", self.generation_panel, "visible", flags)
        self.generation_toggle.set_active(False)
        self.bottom_toggle.set_active(False)

        editor_paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL, vexpand=True, wide_handle=False)
        editor_paned.set_start_child(self.editor)
        editor_paned.set_end_child(self.bottom_panel)
        editor_paned.set_resize_end_child(False)
        editor_paned.set_shrink_end_child(False)
        editor_paned.set_position(480)

        right_paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True)
        right_paned.set_start_child(editor_paned)
        right_paned.set_end_child(self.generation_panel)
        right_paned.set_resize_end_child(False)
        right_paned.set_shrink_end_child(False)

        outer = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, vexpand=True)
        outer.set_start_child(self.files_panel)
        outer.set_end_child(right_paned)
        outer.set_resize_start_child(False)
        outer.set_shrink_start_child(False)
        return outer

    def _build_files_panel(self) -> Gtk.Widget:
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, width_request=200)
        panel.add_css_class("mks-side-panel")
        header = Gtk.Box(spacing=4, margin_start=12, margin_end=6, margin_top=8, margin_bottom=4)
        self.files_title = label("Files", css=("heading",), ellipsize=True)
        self.files_title.set_hexpand(True)
        header.append(self.files_title)
        header.append(icon_button("view-refresh-symbolic", "Refresh", self.refresh_files))
        header.append(icon_button("folder-open-symbolic", "Open the folder in the file manager",
                                  self._reveal_folder))
        panel.append(header)
        self.files_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.files_list.add_css_class("navigation-sidebar")
        self.files_list.connect("row-activated", self._on_file_activated)
        scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_child(self.files_list)
        panel.append(scroller)
        return panel

    def _build_bottom_panel(self) -> Gtk.Widget:
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, height_request=160)
        panel.add_css_class("mks-bottom-panel")
        self.bottom_stack = Gtk.Stack(vexpand=True, transition_type=Gtk.StackTransitionType.CROSSFADE)

        problems = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.problems_summary = label("Press \"Check syntax\" to analyze the code without running it.",
                                      css=("dim-label",), wrap=True)
        self.problems_summary.set_margin_start(12)
        self.problems_summary.set_margin_end(12)
        self.problems_summary.set_margin_top(6)
        self.problems_summary.set_margin_bottom(6)
        problems.append(self.problems_summary)
        self.problems_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.problems_list.add_css_class("mks-problems")
        self.problems_list.connect("row-activated", self._on_problem_activated)
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_child(self.problems_list)
        problems.append(scroller)
        self.bottom_stack.add_titled(problems, "problems", "Problems")

        output = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.output_status = label("No runs in this session.", css=("dim-label",), ellipsize=True)
        self.output_status.set_margin_start(12)
        self.output_status.set_margin_top(6)
        self.output_status.set_margin_bottom(6)
        output.append(self.output_status)
        self.output_view = Gtk.TextView(editable=False, monospace=True, cursor_visible=False,
                                        wrap_mode=Gtk.WrapMode.WORD_CHAR, left_margin=12, right_margin=12,
                                        top_margin=4, bottom_margin=12)
        self.output_view.add_css_class("mks-output")
        self.output_view.update_property([Gtk.AccessibleProperty.LABEL], ["Execution output"])
        self.output_buffer = self.output_view.get_buffer()
        self.output_buffer.create_tag("stderr", foreground="#f28b82")
        self.output_buffer.create_tag("meta", foreground="#7fd6c8", weight=Pango.Weight.BOLD)
        self.output_end_mark = self.output_buffer.create_mark("end", self.output_buffer.get_end_iter(), False)
        output_scroller = Gtk.ScrolledWindow(vexpand=True)
        output_scroller.set_child(self.output_view)
        output.append(output_scroller)
        self.bottom_stack.add_titled(output, "output", "Output")

        header = Gtk.Box(spacing=6, margin_start=8, margin_end=8, margin_top=4, margin_bottom=4)
        switcher = Gtk.StackSwitcher(stack=self.bottom_stack)
        header.append(switcher)
        header.append(Gtk.Box(hexpand=True))
        header.append(icon_button("edit-clear-all-symbolic", "Clear the panel", self._clear_bottom_panel))
        header.append(icon_button("window-close-symbolic", "Close the panel",
                                  lambda: self.bottom_toggle.set_active(False)))
        panel.append(header)
        panel.append(Gtk.Separator())
        panel.append(self.bottom_stack)
        return panel

    def _build_generation_panel(self) -> Gtk.Widget:
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14,
                          margin_start=14, margin_end=14, margin_top=14, margin_bottom=14)
        content.append(label("Generation", css=("title-4",)))
        content.append(label("Adjust the template and apply the result to the editor. "
                             "You can undo it with Ctrl+Z.", css=("dim-label", "caption"), wrap=True))

        templates = self.services.templates.all()
        self._panel_templates: list[Template] = templates
        self.template_dropdown = Gtk.DropDown.new_from_strings([t.name for t in templates])
        self.template_dropdown.update_property([Gtk.AccessibleProperty.LABEL], ["Template"])
        self.template_dropdown.connect("notify::selected", self._on_panel_template_changed)

        group = Adw.PreferencesGroup()
        template_row = Adw.ActionRow(title="Template")
        template_row.add_suffix(self.template_dropdown)
        self.template_dropdown.set_valign(Gtk.Align.CENTER)
        group.add(template_row)
        self.name_row = Adw.EntryRow(title="Script name")
        self.name_row.connect("changed", lambda *_: self._schedule_preview())
        group.add(self.name_row)
        self.description_row = Adw.EntryRow(title="Description")
        self.description_row.connect("changed", lambda *_: self._schedule_preview())
        group.add(self.description_row)
        content.append(group)

        self.panel_form = TemplateForm(self._schedule_preview)
        content.append(self.panel_form)
        self.panel_risk = Gtk.Box()
        content.append(self.panel_risk)
        self.panel_error = label("", css=("error",), wrap=True)
        self.panel_error.set_visible(False)
        content.append(self.panel_error)

        self.apply_button = text_button("Apply to editor", self.regenerate, icon="document-edit-symbolic",
                                        css=("suggested-action", "pill"))
        content.append(self.apply_button)

        expander = Gtk.Expander(label="Preview", expanded=True)
        self.panel_preview = CodeEditor(dark=Adw.StyleManager.get_default().get_dark(), read_only=True)
        self.panel_preview.set_size_request(-1, 320)
        self.panel_preview.add_css_class("mks-preview")
        expander.set_child(self.panel_preview)
        content.append(expander)

        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, width_request=380)
        scroller.set_child(content)
        scroller.add_css_class("mks-side-panel")
        return scroller

    def _build_statusbar(self) -> Gtk.Widget:
        bar = Gtk.Box(spacing=16, margin_start=12, margin_end=12, margin_top=3, margin_bottom=3)
        bar.add_css_class("mks-statusbar")
        self.status_path = label("", ellipsize=True)
        self.status_path.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.status_path.set_hexpand(True)
        self.status_state = label("")
        self.status_position = label("Ln 1, Col 1")
        self.status_lang = label("Bash · UTF-8")
        self.status_indent = label("")
        for widget in (self.status_path, self.status_state, self.status_position, self.status_indent, self.status_lang):
            widget.add_css_class("caption")
            bar.append(widget)
        self.status_state.update_property([Gtk.AccessibleProperty.LABEL], ["File status"])
        return bar

    # ------------------------------------------------------------------
    # Document state
    # ------------------------------------------------------------------

    def _starter_code(self) -> str:
        template = self.services.templates.get("minimal")
        assert template is not None
        return self._generate(template, template.default_values(), UNTITLED, "")

    def _generate(self, template: Template, values: dict[str, Any], name: str, description: str) -> str:
        config = GenerationConfig.from_preferences(self.services.prefs.preferences, name, description)
        return generate_script(template, config, values)

    def _set_document(self, document: Document, text: str, modified: bool = False) -> None:
        self.document = document
        self.editor.set_text(text, modified=modified)
        self._diagnostics = {}
        self._render_problems()
        directory = document.path.parent if document.path else document.save_dir
        self.editor.set_base_directory(directory)
        self._load_generation_panel()
        self.refresh_files()
        self._update_state()
        self.editor.focus()

    def _update_state(self) -> None:
        doc = self.document
        modified = self.editor.is_modified()
        self.status_path.set_text(tilde_path(doc.path) if doc.path else "Not saved")
        if doc.path is None:
            state = "New"
        elif modified:
            state = "● Modified"
        else:
            state = "Saved"
        self.status_state.set_text(state)
        self.status_state.set_css_classes(["caption", "warning"] if modified else ["caption"])
        self.window.set_document_title(doc.title, modified)

    def _on_cursor_moved(self, _editor, line: int, column: int) -> None:
        self.status_position.set_text(f"Ln {line}, Col {column}")

    def _on_text_changed(self, _buffer) -> None:
        if self._diagnostics and not self._diagnostics_stale:
            self._diagnostics_stale = True
            self._render_problems()

    def _apply_preferences(self) -> None:
        prefs = self.services.prefs.preferences
        self.editor.apply_preferences(prefs)
        self.panel_preview.apply_preferences(prefs)
        indent = f"Spaces: {prefs.editor_tab_width}" if prefs.editor_insert_spaces else f"Tab width: {prefs.editor_tab_width}"
        self.status_indent.set_text(indent)
        self.shellcheck_button.set_sensitive(self.services.validation.shellcheck_available)
        if not self.services.validation.shellcheck_available:
            self.shellcheck_button.set_tooltip_text("ShellCheck is not installed (it is optional)")
        self._schedule_preview()

    def _on_dark_changed(self, manager: Adw.StyleManager, _pspec) -> None:
        self.editor.set_dark(manager.get_dark())
        self.panel_preview.set_dark(manager.get_dark())

    def has_unsaved_changes(self) -> bool:
        return self.editor.is_modified()

    def confirm_discard(self, proceed: Callable[[], None]) -> None:
        """Run ``proceed`` after saving or discarding pending changes."""
        if not self.editor.is_modified():
            proceed()
            return

        def on_response(response: str) -> None:
            if response == "save":
                self.save(on_saved=proceed)
            elif response == "discard":
                proceed()

        ask(self, "Save changes?",
            f"\"{self.document.title}\" has unsaved changes. If you discard them, they will be lost.",
            [("cancel", "Cancel", ""), ("discard", "Discard", "destructive"), ("save", "Save", "suggested")],
            on_response, default="save")

    # ------------------------------------------------------------------
    # Files
    # ------------------------------------------------------------------

    def new_document(self, directory: Path | None = None) -> None:
        def proceed() -> None:
            self._set_document(Document(save_dir=directory), self._starter_code())
            self.window.toast("New script created from the minimal template")

        self.confirm_discard(proceed)

    def load_generated(self, template: Template, values: dict[str, Any], script_name: str,
                       description: str, directory: Path | None = None) -> None:
        """Open a generated, not yet saved script in the editor."""

        def proceed() -> None:
            code = self._generate(template, values, script_name, description)
            document = Document(script_name=script_name, description=description, template_id=template.id,
                                values=dict(values), save_dir=directory)
            self._set_document(document, code, modified=True)

        self.confirm_discard(proceed)

    def open_dialog(self) -> None:
        folder = self.document.path.parent if self.document.path else self.services.prefs.preferences.output_path()
        self.confirm_discard(lambda: choose_file_to_open(self.window, self._open_now, folder))

    def open_path(self, path: Path) -> None:
        if self.document.path == path and not self.editor.is_modified():
            self.window.show_section("studio")
            return
        self.confirm_discard(lambda: self._open_now(path))

    def _open_now(self, path: Path) -> None:
        try:
            text = self.services.files.read(path)
        except FileServiceError as exc:
            show_error(self, "Could not open the file", str(exc))
            self.services.state.remove_recent(path)
            return
        document = Document(path=path, script_name=path.name, mtime=path.stat().st_mtime)
        self._set_document(document, text)
        self.services.state.add_recent(path)
        self.window.show_section("studio")

    def save(self, on_saved: Callable[[], None] | None = None) -> None:
        doc = self.document
        if doc.path is None:
            self.save_as(on_saved)
            return
        if doc.mtime is not None and doc.path.exists() and doc.path.stat().st_mtime != doc.mtime:
            ask(self, "The file changed on disk",
                f"Another program modified {tilde_path(doc.path)} after you opened it. "
                "If you save, its content will be replaced (a backup is made if enabled).",
                [("cancel", "Cancel", ""), ("overwrite", "Overwrite", "destructive")],
                lambda r: r == "overwrite" and self._write(doc.path, on_saved, force_backup=True))
            return
        self._write(doc.path, on_saved)

    def save_as(self, on_saved: Callable[[], None] | None = None) -> None:
        doc = self.document
        prefs = self.services.prefs.preferences
        folder = doc.path.parent if doc.path else (doc.save_dir or prefs.output_path())
        try:
            self.services.files.ensure_directory(folder)
        except FileServiceError:
            folder = Path.home()
        try:
            name = normalize_script_name(doc.path.name if doc.path else doc.script_name)
        except InvalidNameError:
            name = UNTITLED
        # The native chooser asks for confirmation if the file already exists.
        choose_file_to_save(self.window, name, folder, lambda path: self._write(path, on_saved, force_backup=True))

    def _write(self, path: Path, on_saved: Callable[[], None] | None = None, force_backup: bool = False) -> None:
        doc = self.document
        prefs = self.services.prefs.preferences
        same_file = doc.path is not None and doc.path == path
        # The original is backed up the first time it is overwritten in the session (or if it is another file).
        needs_backup = force_backup or not same_file or not doc.backup_done
        backup = self.services.backup_service() if needs_backup else None
        try:
            result = self.services.files.save(path, self.editor.get_text(), overwrite=True, backup=backup,
                                              make_executable=prefs.executable_permissions)
        except FileServiceError as exc:
            show_error(self, "Could not save", str(exc))
            return
        doc.path = result.path
        doc.script_name = result.path.name
        doc.mtime = result.path.stat().st_mtime
        doc.backup_done = True
        self.editor.set_modified(False)
        self.editor.set_base_directory(result.path.parent)
        self.services.state.add_recent(result.path)
        message = f"Saved to {tilde_path(result.path)}"
        if result.backup_path:
            message += f" · backup: {result.backup_path.name}"
        self.window.toast(message)
        self.refresh_files()
        self._update_state()
        if on_saved is not None:
            on_saved()

    def refresh_files(self) -> None:
        clear_container(self.files_list)
        self._file_paths = []
        directory = self._files_directory()
        self.files_title.set_text(directory.name if directory else "Files")
        self.files_title.set_tooltip_text(tilde_path(directory) if directory else None)
        if directory is None or not directory.is_dir():
            self.files_list.append(self._placeholder_row("Save the script to see its folder"))
            return
        try:
            entries = sorted((p for p in directory.iterdir() if p.is_file() and not p.name.startswith(".")),
                             key=lambda p: (p.suffix not in (".sh", ".bash"), p.name.lower()))
        except OSError as exc:
            self.files_list.append(self._placeholder_row(f"Cannot read the folder: {exc.strerror}"))
            return
        if not entries:
            self.files_list.append(self._placeholder_row("The folder is empty"))
        for path in entries[:300]:
            row = Gtk.ListBoxRow()
            box = Gtk.Box(spacing=8, margin_start=6, margin_end=6, margin_top=4, margin_bottom=4)
            icon = "utilities-terminal-symbolic" if path.suffix in (".sh", ".bash") else "text-x-generic-symbolic"
            box.append(Gtk.Image(icon_name=icon))
            name = label(path.name, ellipsize=True)
            if path == self.document.path:
                name.add_css_class("accent")
            box.append(name)
            row.set_child(box)
            row.set_tooltip_text(tilde_path(path))
            self.files_list.append(row)
            self._file_paths.append(path)

    @staticmethod
    def _placeholder_row(text: str) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow(activatable=False, selectable=False)
        row.set_child(label(text, css=("dim-label", "caption"), wrap=True))
        return row

    def _files_directory(self) -> Path | None:
        if self.document.path:
            return self.document.path.parent
        if self.document.save_dir:
            return self.document.save_dir
        output = self.services.prefs.preferences.output_path()
        return output if output.is_dir() else None

    def _on_file_activated(self, _list, row: Gtk.ListBoxRow) -> None:
        index = row.get_index()
        if 0 <= index < len(self._file_paths):
            self.open_path(self._file_paths[index])

    def _reveal_folder(self) -> None:
        directory = self._files_directory()
        if directory and directory.is_dir():
            open_in_file_manager(self.window, directory)

    # ------------------------------------------------------------------
    # Search and navigation
    # ------------------------------------------------------------------

    def show_search(self, replace: bool = False) -> None:
        self.window.show_section("studio")
        self.editor.search.open(replace=replace)

    def go_to_line(self) -> None:
        entry = Gtk.Entry(placeholder_text="Line number", input_purpose=Gtk.InputPurpose.DIGITS,
                          activates_default=True)

        def on_response(response: str) -> None:
            if response == "go" and entry.get_text().strip().isdigit():
                self.editor.goto_line(int(entry.get_text().strip()))

        ask(self, "Go to line", f"The file has {self.editor.buffer.get_line_count()} lines.",
            [("cancel", "Cancel", ""), ("go", "Go", "suggested")], on_response, default="go", extra_child=entry)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def check_syntax(self) -> None:
        self._start_check("bash -n", self.services.validation.check_syntax, self.check_button)

    def run_shellcheck(self) -> None:
        if not self.services.validation.shellcheck_available:
            self.window.toast("ShellCheck is not installed; see the README to install it")
            return
        self._start_check("ShellCheck", self.services.validation.run_shellcheck, self.shellcheck_button)

    def _start_check(self, tool: str, check: Callable[[str, CancellableProcess], CheckResult],
                     button: Gtk.Button) -> None:
        self.window.show_section("studio")
        previous = self._check_jobs.get(tool)
        if previous is not None:
            previous.cancel()
        job = CancellableProcess()
        self._check_jobs[tool] = job
        text = self.editor.get_text()
        button.set_sensitive(False)
        self.problems_summary.set_text(f"Running {tool}…")

        def done(result: CheckResult | Exception) -> None:
            if self._check_jobs.get(tool) is not job:
                return  # a newer check was started
            del self._check_jobs[tool]
            button.set_sensitive(tool != "ShellCheck" or self.services.validation.shellcheck_available)
            if isinstance(result, Exception):
                show_error(self, f"Error running {tool}", str(result))
                return
            self._show_check_result(result)

        run_async(lambda: check(text, job), done)

    def _show_check_result(self, result: CheckResult) -> None:
        if result.status in (CheckStatus.OK, CheckStatus.ISSUES):
            self._diagnostics[result.tool] = result.diagnostics
            self._diagnostics_stale = False
        self._last_summary = result.summary
        self._render_problems()
        self.editor.clear_diagnostics()
        for diagnostics in self._diagnostics.values():
            self.editor.add_diagnostics(diagnostics)
        self.bottom_toggle.set_active(True)
        self.bottom_stack.set_visible_child_name("problems")
        self.window.toast(f"{result.tool}: {result.summary}")

    def _render_problems(self) -> None:
        clear_container(self.problems_list)
        all_items = [d for items in self._diagnostics.values() for d in items]
        self._problem_items = sorted(all_items, key=lambda d: (d.line or 0, d.column or 0))
        summary = self._last_summary
        if not self._diagnostics:
            self.problems_summary.set_text(summary or "Press \"Check syntax\" to analyze the code without running it.")
            return
        if self._diagnostics_stale:
            summary = f"{summary} — the code changed after the analysis; check it again."
        elif not all_items:
            summary = "No problems found. Correct syntax does not guarantee that the script is safe."
        self.problems_summary.set_text(summary)
        for diagnostic in self._problem_items:
            row = Adw.ActionRow(activatable=diagnostic.line is not None)
            row.set_use_markup(False)
            row.set_title(diagnostic.message)
            location = f"Line {diagnostic.line}" if diagnostic.line else "No line"
            if diagnostic.column:
                location += f", column {diagnostic.column}"
            source = f"{diagnostic.source} {diagnostic.code or ''}".strip()
            subtitle = f"{location} · {source}"
            if diagnostic.detail:
                subtitle += f" · {diagnostic.detail}"
            row.set_subtitle(subtitle)
            row.set_subtitle_lines(2)
            icon = Gtk.Image(icon_name=_SEVERITY_ICON.get(diagnostic.severity, "dialog-information-symbolic"))
            icon.add_css_class(f"severity-{diagnostic.severity}")
            row.add_prefix(icon)
            self.problems_list.append(row)

    def _on_problem_activated(self, _list, row) -> None:
        index = row.get_index()
        diagnostic = self._problem_items[index] if 0 <= index < len(self._problem_items) else None
        if diagnostic and diagnostic.line:
            self.editor.goto_line(diagnostic.line, diagnostic.column or 0)

    def _clear_bottom_panel(self) -> None:
        if self.bottom_stack.get_visible_child_name() == "output":
            self.output_buffer.set_text("")
            self.output_status.set_text("Output cleared.")
        else:
            self._diagnostics = {}
            self._last_summary = ""
            self.editor.clear_diagnostics()
            self._render_problems()

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def run_script(self) -> None:
        self.window.show_section("studio")
        if self._session is not None and self._session.running:
            self.window.toast("A script is already running")
            return
        if self.document.path is None or self.editor.is_modified():
            ask(self, "Save before running",
                "Only saved files are run, so you know exactly which code is executed.",
                [("cancel", "Cancel", ""), ("save", "Save and continue", "suggested")],
                lambda r: r == "save" and self.save(on_saved=self._confirm_run), default="save")
            return
        self._confirm_run()

    def _confirm_run(self) -> None:
        path = self.document.path
        assert path is not None
        try:
            preview = build_plan(path)
        except ExecutionError as exc:
            show_error(self, "Cannot run", str(exc))
            return
        risks = analyze_risks(self.editor.get_text())

        details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        grid = Gtk.Grid(column_spacing=12, row_spacing=4)
        facts = [
            ("File", tilde_path(path)),
            ("Interpreter", preview.interpreter),
            ("Directory", tilde_path(preview.cwd or path.parent)),
            ("Privileges", "Your user's; sudo is never used"),
            ("Input", "No interactive input (stdin closed)"),
        ]
        for index, (name, value) in enumerate(facts):
            grid.attach(label(name, css=("dim-label",)), 0, index, 1, 1)
            grid.attach(label(value, wrap=True, selectable=True), 1, index, 1, 1)
        details.append(grid)
        args_entry = Gtk.Entry(placeholder_text="Arguments (optional), e.g. --help", activates_default=False)
        args_entry.update_property([Gtk.AccessibleProperty.LABEL], ["Arguments"])
        details.append(args_entry)
        if risks:
            details.append(label("The analysis found commands that may modify the system:",
                                 css=("warning",), wrap=True))
            for risk in risks:
                details.append(label(f"• {risk}", wrap=True, css=("caption",)))

        def on_response(response: str) -> None:
            if response != "run":
                return
            try:
                plan = build_plan(path, args_entry.get_text())
            except ExecutionError as exc:
                show_error(self, "Cannot run", str(exc))
                return
            self._start_session(plan)

        ask(self, "Run this script?",
            "Execution is not sandboxed: the script runs with your permissions and can modify files. "
            "Checking the syntax does not guarantee that it is harmless.",
            [("cancel", "Cancel", ""), ("run", "Run", "destructive" if risks else "suggested")],
            on_response, default="cancel", extra_child=details)

    def _start_session(self, plan) -> None:
        self.bottom_toggle.set_active(True)
        self.bottom_stack.set_visible_child_name("output")
        self._append_output("meta", f"$ {plan.command_line()}\n")
        self.output_status.set_text(f"Running {plan.script.name}…")
        session = ExecutionSession(
            plan,
            on_output=lambda stream, text: idle(self._append_output, stream, text),
            on_exit=lambda code, cancelled: idle(self._on_session_exit, session, code, cancelled),
        )
        try:
            session.start()
        except ExecutionError as exc:
            show_error(self, "Could not run", str(exc))
            return
        self._session = session
        self.run_button.set_sensitive(False)
        self.stop_button.set_visible(True)

    def _append_output(self, stream: str, text: str) -> None:
        end = self.output_buffer.get_end_iter()
        if stream == "stdout":
            self.output_buffer.insert(end, text)
        else:
            self.output_buffer.insert_with_tags_by_name(end, text, "stderr" if stream == "stderr" else "meta")
        self.output_buffer.move_mark(self.output_end_mark, self.output_buffer.get_end_iter())
        self.output_view.scroll_mark_onscreen(self.output_end_mark)

    def _on_session_exit(self, session: ExecutionSession, code: int | None, cancelled: bool) -> None:
        if cancelled:
            status = "Execution cancelled"
        elif code == 0:
            status = "Finished successfully (exit code 0)"
        else:
            status = f"Finished with exit code {code}"
        self._append_output("meta", f"\n[{status}]\n\n")
        self.output_status.set_text(status)
        if self._session is session:
            self._session = None
        self.run_button.set_sensitive(True)
        self.stop_button.set_visible(False)
        self.window.toast(status)

    def stop_script(self) -> None:
        if self._session is not None:
            self._session.cancel()
            self.output_status.set_text("Stopping…")

    def shutdown(self) -> None:
        for job in self._check_jobs.values():
            job.cancel()
        if self._session is not None:
            self._session.cancel(grace_seconds=1.0)

    # ------------------------------------------------------------------
    # Generation panel
    # ------------------------------------------------------------------

    def reload_templates(self) -> None:
        self._panel_templates = self.services.templates.all()
        self._loading_panel = True
        self.template_dropdown.set_model(Gtk.StringList.new([t.name for t in self._panel_templates]))
        self._loading_panel = False
        self._load_generation_panel()

    def _load_generation_panel(self) -> None:
        doc = self.document
        template = self.services.templates.get(doc.template_id) if doc.template_id else None
        template = template or self.services.templates.get("minimal")
        self._loading_panel = True
        index = next((i for i, t in enumerate(self._panel_templates) if t.id == template.id), 0)
        self.template_dropdown.set_selected(index)
        self.name_row.set_text(doc.script_name)
        self.description_row.set_text(doc.description)
        self.panel_form.set_template(template, doc.values if doc.template_id == template.id else None)
        self._loading_panel = False
        self._show_panel_risk(template)
        self._schedule_preview()

    def _panel_template(self) -> Template:
        return self._panel_templates[self.template_dropdown.get_selected()]

    def _on_panel_template_changed(self, *_args) -> None:
        if self._loading_panel:
            return
        template = self._panel_template()
        self.panel_form.set_template(template)
        self._show_panel_risk(template)
        self._schedule_preview()

    def _show_panel_risk(self, template: Template) -> None:
        clear_container(self.panel_risk)
        self.panel_risk.append(risk_banner(template))

    def _schedule_preview(self) -> None:
        if self._loading_panel:
            return
        if self._preview_source:
            GLib.source_remove(self._preview_source)
        self._preview_source = GLib.timeout_add(150, self._update_preview)

    def _panel_code(self) -> tuple[str | None, str | None]:
        """Return ``(code, error)`` for the current panel state."""
        template = self._panel_template()
        values = self.panel_form.values()
        try:
            name = normalize_script_name(self.name_row.get_text())
        except InvalidNameError as exc:
            return None, str(exc)
        _, errors = template.validate(values)
        self.panel_form.show_errors(errors)
        if errors:
            return None, "Fix the highlighted parameters: " + "; ".join(errors.values())
        try:
            return self._generate(template, values, name, self.description_row.get_text()), None
        except GenerationError as exc:
            return None, str(exc)

    def _update_preview(self) -> bool:
        self._preview_source = 0
        code, error = self._panel_code()
        self.panel_error.set_text(error or "")
        self.panel_error.set_visible(bool(error))
        self.apply_button.set_sensitive(code is not None)
        if code is not None:
            self.panel_preview.set_text(code)
        return GLib.SOURCE_REMOVE

    def regenerate(self) -> None:
        code, error = self._panel_code()
        if code is None:
            show_error(self, "Cannot generate", error or "")
            return
        template = self._panel_template()

        def apply() -> None:
            self.editor.replace_text(code)
            doc = self.document
            doc.template_id = template.id
            doc.values = self.panel_form.values()
            doc.description = self.description_row.get_text()
            if doc.path is None:
                doc.script_name = normalize_script_name(self.name_row.get_text())
            self._update_state()
            self.window.toast("Code applied to the editor (Ctrl+Z to undo)")

        if self.editor.get_text().strip() and self.editor.get_text() != code:
            ask(self, "Replace the editor content?",
                "The current code will be replaced with the generated code. You can undo it with Ctrl+Z.",
                [("cancel", "Cancel", ""), ("replace", "Replace", "suggested")],
                lambda r: r == "replace" and apply(), default="replace")
        else:
            apply()

    def save_as_template(self) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        name_entry = Gtk.Entry(placeholder_text="Template name")
        description_entry = Gtk.Entry(placeholder_text="Description (optional)")
        box.append(name_entry)
        box.append(description_entry)
        box.append(label("You can use {{script_name}} and {{description}} in the code; "
                         "they are replaced when generating.", css=("dim-label", "caption"), wrap=True))

        def on_response(response: str) -> None:
            if response != "save":
                return
            try:
                template = self.services.templates.save_custom(
                    name_entry.get_text(), description_entry.get_text(), self.editor.get_text())
            except (ValueError, OSError) as exc:
                show_error(self, "Could not save the template", str(exc))
                return
            self.window.templates_changed()
            self.window.toast(f"Template \"{template.name}\" saved to the library")

        ask(self, "Save as template", "The current editor content will be saved as a custom template.",
            [("cancel", "Cancel", ""), ("save", "Save", "suggested")], on_response, default="save",
            extra_child=box)
