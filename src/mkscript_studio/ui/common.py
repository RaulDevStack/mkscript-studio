"""Utilidades de interfaz compartidas: diálogos, hilos y selección de archivos."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk, Pango  # noqa: E402

Response = tuple[str, str, str]  # (id, etiqueta, apariencia: "", "suggested", "destructive")

_APPEARANCE = {
    "suggested": Adw.ResponseAppearance.SUGGESTED,
    "destructive": Adw.ResponseAppearance.DESTRUCTIVE,
}


def ask(
    parent: Gtk.Widget,
    heading: str,
    body: str,
    responses: list[Response],
    callback: Callable[[str], None],
    default: str | None = None,
    close: str = "cancel",
    extra_child: Gtk.Widget | None = None,
) -> None:
    """Diálogo nativo de libadwaita. ``callback`` recibe el id de la respuesta."""
    if hasattr(Adw, "AlertDialog"):
        dialog = Adw.AlertDialog(heading=heading, body=body)
    else:  # libadwaita < 1.5
        dialog = Adw.MessageDialog(heading=heading, body=body, transient_for=parent.get_root(), modal=True)
    for response_id, label, appearance in responses:
        dialog.add_response(response_id, label)
        if appearance in _APPEARANCE:
            dialog.set_response_appearance(response_id, _APPEARANCE[appearance])
    if default:
        dialog.set_default_response(default)
    dialog.set_close_response(close)
    if extra_child is not None:
        dialog.set_extra_child(extra_child)
    dialog.connect("response", lambda _d, response: callback(response))
    if hasattr(Adw, "AlertDialog"):
        dialog.present(parent)
    else:
        dialog.present()


def show_error(parent: Gtk.Widget, heading: str, message: str) -> None:
    ask(parent, heading, message, [("close", "Cerrar", "")], lambda _r: None, default="close", close="close")


def run_async(func: Callable[[], Any], on_done: Callable[[Any], None]) -> threading.Thread:
    """Ejecuta ``func`` en un hilo y entrega el resultado (o la excepción) en el hilo de GTK."""

    def worker() -> None:
        try:
            result: Any = func()
        except Exception as exc:  # se entrega a la interfaz para mostrarlo
            result = exc
        GLib.idle_add(_deliver, on_done, result)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread


def _deliver(on_done: Callable[[Any], None], result: Any) -> bool:
    on_done(result)
    return GLib.SOURCE_REMOVE


def idle(func: Callable[..., Any], *args: Any) -> None:
    """Programa ``func`` en el hilo principal (para callbacks desde otros hilos)."""

    def call() -> bool:
        func(*args)
        return GLib.SOURCE_REMOVE

    GLib.idle_add(call)


def _script_filters() -> tuple[Gio.ListStore, Gtk.FileFilter]:
    scripts = Gtk.FileFilter()
    scripts.set_name("Scripts de shell")
    scripts.add_pattern("*.sh")
    scripts.add_pattern("*.bash")
    scripts.add_mime_type("application/x-shellscript")
    everything = Gtk.FileFilter()
    everything.set_name("Todos los archivos")
    everything.add_pattern("*")
    store = Gio.ListStore.new(Gtk.FileFilter)
    store.append(scripts)
    store.append(everything)
    return store, scripts


def _finish(dialog: Gtk.FileDialog, method: str, result: Gio.AsyncResult, callback: Callable[[Path], None]) -> None:
    try:
        gfile = getattr(dialog, method)(result)
    except GLib.Error:
        return  # cancelado por el usuario
    if gfile is not None and gfile.get_path():
        callback(Path(gfile.get_path()))


def choose_file_to_open(parent: Gtk.Window, callback: Callable[[Path], None], folder: Path | None = None) -> None:
    dialog = Gtk.FileDialog(title="Abrir script", modal=True)
    filters, default = _script_filters()
    dialog.set_filters(filters)
    dialog.set_default_filter(default)
    if folder is not None and folder.is_dir():
        dialog.set_initial_folder(Gio.File.new_for_path(str(folder)))
    dialog.open(parent, None, lambda d, r: _finish(d, "open_finish", r, callback))


def choose_file_to_save(parent: Gtk.Window, name: str, folder: Path | None, callback: Callable[[Path], None]) -> None:
    """El selector de GTK pide confirmación si el archivo elegido ya existe."""
    dialog = Gtk.FileDialog(title="Guardar script", modal=True)
    filters, default = _script_filters()
    dialog.set_filters(filters)
    dialog.set_default_filter(default)
    if folder is not None and folder.is_dir():
        dialog.set_initial_folder(Gio.File.new_for_path(str(folder)))
    dialog.set_initial_name(name)
    dialog.save(parent, None, lambda d, r: _finish(d, "save_finish", r, callback))


def choose_folder(parent: Gtk.Window, callback: Callable[[Path], None], folder: Path | None = None) -> None:
    dialog = Gtk.FileDialog(title="Elegir carpeta", modal=True)
    if folder is not None and folder.is_dir():
        dialog.set_initial_folder(Gio.File.new_for_path(str(folder)))
    dialog.select_folder(parent, None, lambda d, r: _finish(d, "select_folder_finish", r, callback))


def open_in_file_manager(parent: Gtk.Window, path: Path) -> None:
    launcher = Gtk.FileLauncher.new(Gio.File.new_for_path(str(path)))
    launcher.launch(parent, None, None)


def tilde_path(path: Path | str) -> str:
    """Muestra las rutas de la carpeta personal con ``~``."""
    text = str(path)
    home = str(Path.home())
    if text == home:
        return "~"
    if text.startswith(home + "/"):
        return "~" + text[len(home):]
    return text


def clear_container(container: Gtk.Widget) -> None:
    child = container.get_first_child()
    while child is not None:
        following = child.get_next_sibling()
        container.remove(child)
        child = following


def label(text: str = "", css: tuple[str, ...] = (), xalign: float = 0.0, wrap: bool = False,
          ellipsize: bool = False, selectable: bool = False) -> Gtk.Label:
    widget = Gtk.Label(label=text, xalign=xalign, wrap=wrap, selectable=selectable)
    if wrap:
        widget.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    if ellipsize:
        widget.set_ellipsize(Pango.EllipsizeMode.END)
    for name in css:
        widget.add_css_class(name)
    return widget


def icon_button(icon: str, tooltip: str, callback: Callable[[], None], css: tuple[str, ...] = ("flat",)) -> Gtk.Button:
    button = Gtk.Button(icon_name=icon, tooltip_text=tooltip)
    button.update_property([Gtk.AccessibleProperty.LABEL], [tooltip])
    for name in css:
        button.add_css_class(name)
    button.connect("clicked", lambda _b: callback())
    return button


def text_button(text: str, callback: Callable[[], None], icon: str | None = None,
                css: tuple[str, ...] = (), tooltip: str | None = None) -> Gtk.Button:
    button = Gtk.Button()
    if icon:
        content = Adw.ButtonContent(icon_name=icon, label=text, use_underline=True)
        button.set_child(content)
    else:
        button.set_label(text)
        button.set_use_underline(True)
    if tooltip:
        button.set_tooltip_text(tooltip)
    for name in css:
        button.add_css_class(name)
    button.connect("clicked", lambda _b: callback())
    return button
