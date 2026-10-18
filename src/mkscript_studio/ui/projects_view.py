"""Projects: folders of related scripts."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from mkscript_studio.services.project_service import Project, ProjectError  # noqa: E402
from mkscript_studio.ui.common import (  # noqa: E402
    ask,
    choose_folder,
    clear_container,
    label,
    open_in_file_manager,
    show_error,
    text_button,
    tilde_path,
)

if TYPE_CHECKING:
    from mkscript_studio.context import AppServices
    from mkscript_studio.window import MainWindow


class ProjectsView(Gtk.Box):
    __gtype_name__ = "MksProjectsView"

    def __init__(self, window: MainWindow, services: AppServices) -> None:
        super().__init__()
        self.window = window
        self.services = services
        self._projects: list[Project] = []
        self.selected: Project | None = None

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, vexpand=True)
        paned.set_start_child(self._build_list())
        paned.set_end_child(self._build_detail())
        paned.set_resize_start_child(False)
        paned.set_shrink_start_child(False)
        paned.set_position(300)
        self.append(paned)
        # Re-list the files when returning to this section (they may have changed).
        self.connect("map", lambda *_: self._show_detail())
        self.refresh()

    def _build_list(self) -> Gtk.Widget:
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, width_request=260,
                         margin_start=12, margin_end=12, margin_top=12, margin_bottom=12)
        buttons = Gtk.Box(spacing=6, homogeneous=True)
        buttons.append(text_button("New", self.create_project, icon="folder-new-symbolic", css=("suggested-action",)))
        buttons.append(text_button("Add folder", self.add_existing, icon="folder-open-symbolic"))
        column.append(buttons)
        self.project_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.project_list.add_css_class("navigation-sidebar")
        self.project_list.connect("row-selected", self._on_selected)
        scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_child(self.project_list)
        column.append(scroller)
        return column

    def _build_detail(self) -> Gtk.Widget:
        self.empty_page = Adw.StatusPage(
            icon_name="folder-symbolic", title="No projects",
            description="A project groups related scripts in a folder. Create a new one "
                        "or add an existing folder.")
        self.detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16,
                              margin_start=24, margin_end=24, margin_top=20, margin_bottom=24)
        self.name_label = label("", css=("title-2",), wrap=True)
        self.path_label = label("", css=("dim-label",), wrap=True, selectable=True)
        self.detail.append(self.name_label)
        self.detail.append(self.path_label)

        actions = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=6,
                              column_spacing=6, row_spacing=6)
        actions.append(text_button("New script here", self._new_script, icon="document-new-symbolic",
                                   css=("suggested-action",)))
        actions.append(text_button("Open folder", self._reveal, icon="folder-open-symbolic"))
        actions.append(text_button("Rename", self._rename, icon="document-edit-symbolic"))
        actions.append(text_button("Duplicate", self._duplicate, icon="edit-copy-symbolic"))
        actions.append(text_button("Remove from list", self._remove, icon="list-remove-symbolic"))
        self.detail.append(actions)

        self.files_group = Adw.PreferencesGroup(title="Files")
        self.detail.append(self.files_group)
        self._file_rows: list[Gtk.Widget] = []

        clamp = Adw.Clamp(maximum_size=900)
        clamp.set_child(self.detail)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_child(clamp)
        self.stack = Gtk.Stack()
        self.stack.add_named(self.empty_page, "empty")
        self.stack.add_named(scroller, "detail")
        return self.stack

    # -- data --------------------------------------------------------------

    def refresh(self, select: Path | None = None) -> None:
        previous = select or (self.selected.path if self.selected else None)
        self._projects = self.services.projects.list_projects()
        clear_container(self.project_list)
        for project in self._projects:
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=6, margin_bottom=6,
                          margin_start=6, margin_end=6)
            box.append(label(project.name, css=("heading",), ellipsize=True))
            subtitle = tilde_path(project.path) if project.exists else "Folder not found"
            box.append(label(subtitle, css=("dim-label", "caption"), ellipsize=True))
            row = Gtk.ListBoxRow()
            row.set_child(box)
            self.project_list.append(row)
        if not self._projects:
            self.selected = None
            self.stack.set_visible_child_name("empty")
            return
        self.stack.set_visible_child_name("detail")
        index = next((i for i, p in enumerate(self._projects) if p.path == previous), 0)
        self.project_list.select_row(self.project_list.get_row_at_index(index))

    def _on_selected(self, _list, row: Gtk.ListBoxRow | None) -> None:
        if row is None or not (0 <= row.get_index() < len(self._projects)):
            return
        self.selected = self._projects[row.get_index()]
        self._show_detail()

    def _show_detail(self) -> None:
        project = self.selected
        if project is None:
            return
        self.name_label.set_text(project.name)
        self.path_label.set_text(tilde_path(project.path) + ("" if project.exists else " (does not exist)"))
        for row in self._file_rows:
            self.files_group.remove(row)
        self._file_rows = []
        files = self.services.projects.files(project)
        if not files:
            placeholder = Adw.ActionRow(title="No files", subtitle="Create a script with \"New script here\".")
            self.files_group.add(placeholder)
            self._file_rows.append(placeholder)
        for path in files:
            row = Adw.ActionRow(title=path.name, activatable=True)
            row.set_use_markup(False)
            row.add_prefix(Gtk.Image(icon_name="utilities-terminal-symbolic" if path.suffix == ".sh"
                                     else "text-x-generic-symbolic"))
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.connect("activated", lambda _r, p=path: self.window.studio.open_path(p))
            self.files_group.add(row)
            self._file_rows.append(row)

    # -- actions -------------------------------------------------------------

    def _name_dialog(self, heading: str, body: str, initial: str, action: str, callback) -> None:
        entry = Gtk.Entry(text=initial, activates_default=True)
        ask(self, heading, body, [("cancel", "Cancel", ""), ("ok", action, "suggested")],
            lambda r: r == "ok" and callback(entry.get_text()), default="ok", extra_child=entry)

    def create_project(self) -> None:
        parent = self.services.prefs.preferences.output_path()

        def create(name: str) -> None:
            try:
                self.services.files.ensure_directory(parent)
                project = self.services.projects.create(name, parent)
            except (ProjectError, OSError) as exc:
                show_error(self, "Could not create the project", str(exc))
                return
            self.refresh(select=project.path)
            self.window.toast(f"Project created at {tilde_path(project.path)}")

        self._name_dialog("New project", f"A folder will be created inside {tilde_path(parent)}.",
                          "", "Create", create)

    def add_existing(self) -> None:
        def add(path: Path) -> None:
            try:
                project = self.services.projects.add_existing(path)
            except ProjectError as exc:
                show_error(self, "Could not add the folder", str(exc))
                return
            self.refresh(select=project.path)

        choose_folder(self.window, add, self.services.prefs.preferences.output_path())

    def _new_script(self) -> None:
        if self.selected and self.selected.exists:
            self.window.show_section("studio")
            self.window.studio.new_document(directory=self.selected.path)

    def _reveal(self) -> None:
        if self.selected and self.selected.exists:
            open_in_file_manager(self.window, self.selected.path)

    def _rename(self) -> None:
        project = self.selected
        if project is None:
            return

        def rename(name: str) -> None:
            try:
                self.services.projects.rename(project, name)
            except ProjectError as exc:
                show_error(self, "Could not rename", str(exc))
                return
            self.refresh()

        self._name_dialog("Rename project", "Changes the displayed name; the folder is not moved.",
                          project.name, "Rename", rename)

    def _duplicate(self) -> None:
        project = self.selected
        if project is None:
            return

        def duplicate(name: str) -> None:
            try:
                copy = self.services.projects.duplicate(project, name)
            except ProjectError as exc:
                show_error(self, "Could not duplicate", str(exc))
                return
            self.refresh(select=copy.path)
            self.window.toast(f"Project duplicated at {tilde_path(copy.path)}")

        self._name_dialog("Duplicate project", "The whole folder will be copied next to the original.",
                          f"{project.name} copy", "Duplicate", duplicate)

    def _remove(self) -> None:
        project = self.selected
        if project is None:
            return

        def remove(response: str) -> None:
            if response == "remove":
                self.services.projects.remove_from_list(project)
                self.selected = None
                self.refresh()

        ask(self, "Remove from list?",
            f"\"{project.name}\" will no longer be listed here. The folder and its files are not deleted.",
            [("cancel", "Cancel", ""), ("remove", "Remove", "destructive")], remove)
