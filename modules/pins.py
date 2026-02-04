import cairo
import json
import os
import re
import subprocess
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk

from fabric.widgets.box import Box
from fabric.widgets.label import Label
from fabric.widgets.scrolledwindow import ScrolledWindow
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

import config.data as data
import modules.icons as icons


# Константы
SAVE_FILE = os.path.expanduser("~/.pins.json")
DEFAULT_ICON_SIZE = 80
COMPACT_ICON_SIZE = 36
FAVICON_SIZE_DEFAULT = 48
FAVICON_SIZE_COMPACT = 36

# Размеры сетки
GRID_COMPACT = (10, 3)  # rows, columns для компактного режима
GRID_NORMAL = (3, 5)    # rows, columns для обычного режима

# URL regex pattern
URL_PATTERN = re.compile(
    r'^(https?|ftp)://'
    r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+(?:[A-Z]{2,6}\.?|[A-Z0-9-]{2,}\.?)|'
    r'localhost|'
    r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'
    r'(?::\d+)?'
    r'(?:/?|[/?]\S+)$',
    re.IGNORECASE
)


def get_icon_size() -> int:
    """Получить размер иконок на основе настроек темы"""
    is_compact = (
        data.PANEL_THEME == "Panel" and 
        (data.BAR_POSITION in ["Left", "Right"] or 
         data.PANEL_POSITION in ["Start", "End"])
    )
    return COMPACT_ICON_SIZE if is_compact else DEFAULT_ICON_SIZE


def create_surface_from_widget(widget: Gtk.Widget) -> cairo.ImageSurface:
    """Создать Cairo surface из GTK виджета для drag-and-drop"""
    alloc = widget.get_allocation()
    surface = cairo.ImageSurface(cairo.Format.ARGB32, alloc.width, alloc.height)
    cr = cairo.Context(surface)

    # Прозрачный фон
    cr.set_source_rgba(1, 1, 1, 0)
    cr.rectangle(0, 0, alloc.width, alloc.height)
    cr.fill()

    widget.draw(cr)
    return surface


def open_with_xdg(path: str, is_url: bool = False):
    """Открыть файл или URL с помощью xdg-open"""
    try:
        subprocess.Popen(["xdg-open", path])
    except Exception as e:
        resource_type = "URL" if is_url else "file"
        print(f"Error opening {resource_type}: {e}")


def is_url(text: str) -> bool:
    """Проверить, является ли текст валидным URL"""
    return bool(URL_PATTERN.match(text))


def extract_domain(url: str) -> str:
    """Извлечь домен из URL для отображения"""
    domain = re.sub(r'^https?://', '', url)
    return domain.split('/')[0]


def get_favicon_url(url: str) -> str:
    """Получить URL фавикона для данного URL"""
    parsed_url = urllib.parse.urlparse(url)
    base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
    return f"{base_url}/favicon.ico"


def download_favicon(url: str, callback: Callable[[Optional[str]], None]):
    """Асинхронно скачать фавикон и вызвать callback с путём к файлу"""
    favicon_url = get_favicon_url(url)

    def do_download():
        temp_file = None
        try:
            temp_fd, temp_path = tempfile.mkstemp(suffix='.ico')
            os.close(temp_fd)
            temp_file = temp_path

            urllib.request.urlretrieve(favicon_url, temp_path)
            GLib.idle_add(callback, temp_path)
        except Exception as e:
            print(f"Error downloading favicon: {e}")
            if temp_file and os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except Exception:
                    pass
            GLib.idle_add(callback, None)

    GLib.Thread.new("favicon-download", do_download, None)


class FileChangeHandler(FileSystemEventHandler):
    """Обработчик изменений файловой системы для отслеживания закреплённых файлов"""

    def __init__(self, app):
        super().__init__()
        self.app = app

    def on_any_event(self, event):
        """Обработать любое событие файловой системы"""
        if event.is_directory:
            return

        for cell in self.app.cells:
            if cell.content_type == 'file' and cell.content:
                try:
                    cell_real = os.path.realpath(cell.content)
                    src_real = os.path.realpath(event.src_path)
                    dest_real = os.path.realpath(getattr(event, 'dest_path', ''))

                    if cell_real == src_real or (dest_real and cell_real == dest_real):
                        GLib.idle_add(self._handle_file_event, cell, event)
                except Exception:
                    pass

    def _handle_file_event(self, cell, event):
        """Обработать событие для конкретной ячейки"""
        if event.event_type == 'deleted':
            cell.clear_cell()
            self.app.save_state()
        elif event.event_type == 'moved':
            if hasattr(event, 'dest_path') and os.path.exists(event.dest_path):
                cell.content = event.dest_path
                cell.update_display()
                self.app.save_state()
                self.app.add_monitor_for_path(os.path.dirname(event.dest_path))


class Cell(Gtk.EventBox):
    """Ячейка для хранения файла, URL или текста с поддержкой drag-and-drop"""

    def __init__(self, app, content: Optional[str] = None, content_type: Optional[str] = None):
        super().__init__(name="pin-cell")
        self.app = app
        self.content = content
        self.content_type = content_type
        self.favicon_temp_path: Optional[str] = None

        # Основной контейнер
        self.box = Box(name="pin-cell-box", orientation="v", spacing=4)
        self.add(self.box)

        # Настройка drag-and-drop
        self._setup_drag_dest()
        self._setup_drag_source()

        # Обработчики событий
        self.connect("button-press-event", self.on_button_press)
        self.connect("drag-begin", self.on_drag_begin)

        self.update_display()

    def _setup_drag_dest(self):
        """Настроить ячейку как цель для drag-and-drop"""
        target = Gtk.TargetEntry.new("text/uri-list", 0, 0)
        self.drag_dest_set(Gtk.DestDefaults.ALL, [target], Gdk.DragAction.COPY)
        self.connect("drag-data-received", self.on_drag_data_received)

    def _setup_drag_source(self):
        """Настроить ячейку как источник для drag-and-drop"""
        targets = [
            Gtk.TargetEntry.new("text/uri-list", 0, 0),
            Gtk.TargetEntry.new("text/plain", 0, 1)
        ]
        self.drag_source_set(Gdk.ModifierType.BUTTON1_MASK, targets, Gdk.DragAction.COPY)
        self.connect("drag-data-get", self.on_drag_data_get)

    def _cleanup_favicon(self):
        """Удалить временный файл фавикона"""
        if self.favicon_temp_path and os.path.exists(self.favicon_temp_path):
            try:
                os.remove(self.favicon_temp_path)
                self.favicon_temp_path = None
            except Exception as e:
                print(f"Error removing temp favicon: {e}")

    def _clear_box(self):
        """Очистить содержимое box"""
        for child in self.box.get_children():
            self.box.remove(child)

    def update_display(self):
        """Обновить отображение ячейки"""
        self._cleanup_favicon()
        self._clear_box()

        if self.content is None:
            self._display_empty_cell()
        elif self.content_type == 'file':
            self._display_file()
        elif self.content_type == 'text':
            self._display_text()

        self.box.show_all()

        if not self.app.loading_state:
            self.app.save_state()

    def _display_empty_cell(self):
        """Отобразить пустую ячейку"""
        label = Label(name="pin-add", markup=icons.paperclip)
        self.box.pack_start(label, True, True, 0)

    def _display_file(self):
        """Отобразить файл"""
        widget = self._get_file_preview(self.content)
        self.box.pack_start(widget, True, True, 0)

        filename = os.path.basename(self.content)
        label = Label(
            name="pin-file",
            label=filename,
            justification="center",
            ellipsization="middle"
        )
        self.box.pack_start(label, False, False, 0)

    def _display_text(self):
        """Отобразить текст или URL"""
        if is_url(self.content):
            self._display_url()
        else:
            self._display_plain_text()

    def _display_url(self):
        """Отобразить URL с иконкой"""
        icon_container = Box(name="pin-icon-container", orientation="v")
        self.box.pack_start(icon_container, True, True, 0)

        icon_size = get_icon_size()
        url_icon = Label(
            name="pin-url-icon",
            markup=icons.world,
            style=f"font-size: {icon_size}px;"
        )
        icon_container.pack_start(url_icon, True, True, 0)

        domain = extract_domain(self.content)
        label = Label(
            name="pin-url",
            label=domain,
            justification="center",
            ellipsization="end"
        )
        self.box.pack_start(label, False, False, 0)

        # Скачать фавикон асинхронно
        download_favicon(
            self.content,
            lambda path: self._update_favicon(icon_container, url_icon, path)
        )

    def _display_plain_text(self):
        """Отобразить обычный текст"""
        text = self.content.split('\n')[0]
        label = Label(
            name="pin-text",
            label=text,
            justification="center",
            ellipsization="end",
            line_wrap="word-char"
        )
        self.box.pack_start(label, True, True, 0)

    def _update_favicon(self, container: Box, icon_widget: Label, favicon_path: Optional[str]):
        """Обновить иконку фавиконом"""
        if not favicon_path or not os.path.exists(favicon_path):
            return

        try:
            self.favicon_temp_path = favicon_path

            # Определить размер фавикона
            is_compact = (
                data.PANEL_THEME == "Panel" and 
                data.BAR_POSITION in ["Left", "Right"]
            )
            size = FAVICON_SIZE_COMPACT if is_compact else FAVICON_SIZE_DEFAULT

            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                favicon_path, width=size, height=size, preserve_aspect_ratio=True
            )

            container.remove(icon_widget)
            img = Gtk.Image.new_from_pixbuf(pixbuf)
            img.set_name("pin-favicon")
            container.pack_start(img, True, True, 0)
            container.show_all()
        except Exception as e:
            print(f"Error setting favicon: {e}")

    def _get_file_preview(self, filepath: str) -> Gtk.Widget:
        """Получить виджет предпросмотра файла"""
        content_type = self._get_content_type(filepath)
        icon_theme = Gtk.IconTheme.get_default()
        icon_size = get_icon_size()

        # Обработка папок
        if content_type == "inode/directory":
            return self._load_icon(icon_theme, "default-folder", icon_size)

        # Обработка изображений
        if content_type and content_type.startswith("image/"):
            return self._load_image_preview(filepath, icon_size)

        # Обработка видео
        if content_type and content_type.startswith("video/"):
            return self._load_icon(icon_theme, "video-x-generic", icon_size)

        # Обработка других файлов
        return self._load_generic_icon(content_type, icon_theme, icon_size)

    def _get_content_type(self, filepath: str) -> Optional[str]:
        """Получить MIME-тип файла"""
        try:
            file = Gio.File.new_for_path(filepath)
            info = file.query_info(
                "standard::content-type",
                Gio.FileQueryInfoFlags.NONE,
                None
            )
            return info.get_content_type()
        except Exception:
            return None

    def _load_icon(
        self,
        icon_theme: Gtk.IconTheme,
        icon_name: str,
        size: int
    ) -> Gtk.Image:
        """Загрузить иконку из темы"""
        try:
            pixbuf = icon_theme.load_icon(icon_name, size, 0)
            return Gtk.Image.new_from_pixbuf(pixbuf)
        except Exception:
            print(f"Error loading icon: {icon_name}")
            return Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.DIALOG)

    def _load_image_preview(self, filepath: str, size: int) -> Gtk.Widget:
        """Загрузить превью изображения"""
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                filepath, width=size, height=size, preserve_aspect_ratio=True
            )
            return Gtk.Image.new_from_pixbuf(pixbuf)
        except Exception as e:
            print(f"Error loading image preview: {e}")
            return Gtk.Image.new_from_icon_name("image-x-generic", Gtk.IconSize.DIALOG)

    def _load_generic_icon(
        self,
        content_type: Optional[str],
        icon_theme: Gtk.IconTheme,
        size: int
    ) -> Gtk.Widget:
        """Загрузить иконку для обычного файла"""
        icon_name = "text-x-generic"

        if content_type:
            themed_icon = Gio.content_type_get_icon(content_type)
            if hasattr(themed_icon, 'get_names'):
                names = themed_icon.get_names()
                if names:
                    icon_name = names[0]

        return self._load_icon(icon_theme, icon_name, size)

    def on_drag_data_received(self, widget, drag_context, x, y, data, info, time):
        """Обработать получение данных через drag-and-drop"""
        if self.content is None and data.get_length() >= 0:
            uris = data.get_uris()
            if uris:
                try:
                    filepath, _ = GLib.filename_from_uri(uris[0])
                    self.content = filepath
                    self.content_type = 'file'
                    self.update_display()
                except Exception as e:
                    print(f"Error getting file from URI: {e}")

        drag_context.finish(True, False, time)

    def on_drag_data_get(self, widget, drag_context, data, info, time):
        """Предоставить данные для drag-and-drop"""
        if self.content is None:
            return

        if info == 0 and self.content_type == 'file':
            uri = GLib.filename_to_uri(self.content)
            data.set_uris([uri])
        elif info == 1 and self.content_type == 'text':
            data.set_text(self.content, -1)

    def on_drag_begin(self, widget, context):
        """Начало операции drag"""
        if self.content_type == 'file':
            surface = create_surface_from_widget(self)
            Gtk.drag_set_icon_surface(context, surface)

    def on_button_press(self, widget, event) -> bool:
        """Обработать нажатие кнопки мыши"""
        if self.content is None:
            self._handle_empty_cell_click(event)
        else:
            self._handle_filled_cell_click(event)
        return True

    def _handle_empty_cell_click(self, event):
        """Обработать клик по пустой ячейке"""
        if event.button == 1:  # Левая кнопка
            self._select_file()
        elif event.button == 2:  # Средняя кнопка - вставить из буфера
            self._paste_from_clipboard()

    def _handle_filled_cell_click(self, event):
        """Обработать клик по заполненной ячейке"""
        if self.content_type == 'file':
            self._handle_file_click(event)
        elif self.content_type == 'text':
            self._handle_text_click(event)

    def _handle_file_click(self, event):
        """Обработать клик по файлу"""
        if event.button == 1 and event.type == Gdk.EventType._2BUTTON_PRESS:
            # Двойной клик - открыть файл
            open_with_xdg(self.content, is_url=False)
        elif event.button == 3:  # Правая кнопка - очистить
            self.clear_cell()

    def _handle_text_click(self, event):
        """Обработать клик по тексту"""
        if event.button == 1:  # Левая кнопка
            clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            clipboard.set_text(self.content, -1)

            # Открыть URL если не зажат Ctrl
            if is_url(self.content) and not (event.state & Gdk.ModifierType.CONTROL_MASK):
                open_with_xdg(self.content, is_url=True)
        elif event.button == 3:  # Правая кнопка - очистить
            self.clear_cell()

    def _select_file(self):
        """Открыть диалог выбора файла"""
        dialog = Gtk.FileChooserDialog(
            title="Select File",
            parent=self.get_toplevel(),
            action=Gtk.FileChooserAction.OPEN
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OPEN, Gtk.ResponseType.OK
        )

        if dialog.run() == Gtk.ResponseType.OK:
            filepath = dialog.get_filename()
            self.content = filepath
            self.content_type = 'file'
            self.update_display()

        dialog.destroy()

    def _paste_from_clipboard(self):
        """Вставить текст из буфера обмена"""
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        text = clipboard.wait_for_text()
        if text:
            self.content = text
            self.content_type = 'text'
            self.update_display()

    def clear_cell(self):
        """Очистить ячейку"""
        self._cleanup_favicon()
        self.content = None
        self.content_type = None
        self.update_display()


class Pins(Gtk.Box):
    """Виджет для закрепления файлов, URL и текста"""

    def __init__(self, **kwargs):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        self.loading_state = True
        self.cells: List[Cell] = []
        self.monitored_paths: set = set()

        # Настройка файлового мониторинга
        self.observer = Observer()
        self.event_handler = FileChangeHandler(self)

        # Создание UI
        self._setup_grid()

        # Загрузка состояния
        self.load_state()
        self.loading_state = False

        # Запуск мониторинга
        self._start_file_monitoring()

        # Настройка drag-and-drop на весь виджет
        self._setup_widget_drag()

    def _get_grid_dimensions(self) -> tuple:
        """Получить размеры сетки на основе настроек"""
        is_compact = (
            data.PANEL_THEME == "Panel" and 
            (data.BAR_POSITION in ["Left", "Right"] or 
             data.PANEL_POSITION in ["Start", "End"])
        )
        return GRID_COMPACT if is_compact else GRID_NORMAL

    def _setup_grid(self):
        """Создать и настроить сетку ячеек"""
        rows, cols = self._get_grid_dimensions()

        grid = Gtk.Grid(row_spacing=8, column_spacing=8, name="pin-grid")
        grid.set_column_homogeneous(True)
        grid.set_row_homogeneous(True)

        # Создание ячеек
        for row in range(rows):
            for col in range(cols):
                cell = Cell(self)
                self.cells.append(cell)
                grid.attach(cell, col, row, 1, 1)

        # Обёртка в ScrolledWindow
        scrolled_window = ScrolledWindow(
            child=grid,
            name="scrolled-window",
            style_classes="pins",
            propagate_width=False,
            propagate_height=False
        )
        scrolled_window.set_hexpand(True)
        scrolled_window.set_vexpand(True)
        scrolled_window.set_halign(Gtk.Align.FILL)
        scrolled_window.set_valign(Gtk.Align.FILL)

        self.pack_start(scrolled_window, True, True, 0)

    def _setup_widget_drag(self):
        """Настроить drag-and-drop для всего виджета"""
        self.drag_dest_set(Gtk.DestDefaults.ALL, [], Gdk.DragAction.COPY)
        self.connect("drag-data-received", self.on_drag_data_received)

    def _start_file_monitoring(self):
        """Запустить мониторинг файловой системы"""
        for cell in self.cells:
            if cell.content_type == 'file' and cell.content:
                self._add_monitor_for_file(cell.content)

        self.observer.start()

    def _add_monitor_for_file(self, filepath: str):
        """Добавить мониторинг для файла"""
        dir_path = os.path.dirname(filepath)
        if os.path.exists(dir_path) and dir_path not in self.monitored_paths:
            self.observer.schedule(self.event_handler, dir_path, recursive=False)
            self.monitored_paths.add(dir_path)

    def add_monitor_for_path(self, path: str):
        """Добавить мониторинг для пути"""
        if path not in self.monitored_paths and os.path.exists(path):
            self.observer.schedule(self.event_handler, path, recursive=False)
            self.monitored_paths.add(path)

    def save_state(self):
        """Сохранить состояние всех ячеек в JSON файл"""
        state = [
            {
                'content_type': cell.content_type,
                'content': cell.content
            }
            for cell in self.cells
        ]

        try:
            with open(SAVE_FILE, 'w') as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            print(f"Error saving state: {e}")

    def load_state(self):
        """Загрузить состояние из JSON файла"""
        if not os.path.exists(SAVE_FILE):
            return

        try:
            with open(SAVE_FILE, 'r') as f:
                state = json.load(f)

            for i, cell_data in enumerate(state):
                if i < len(self.cells):
                    content = cell_data.get('content')
                    content_type = cell_data.get('content_type')
                    self.cells[i].content = content
                    self.cells[i].content_type = content_type
                    self.cells[i].update_display()
        except Exception as e:
            print(f"Error loading state: {e}")

    def on_drag_data_received(self, widget, drag_context, x, y, data, info, time):
        """Обработать drop файлов на виджет"""
        if data.get_length() >= 0:
            uris = data.get_uris()
            for uri in uris:
                try:
                    filepath, _ = GLib.filename_from_uri(uri)
                    # Найти первую пустую ячейку
                    for cell in self.cells:
                        if cell.content is None:
                            cell.content = filepath
                            cell.content_type = 'file'
                            cell.update_display()
                            break
                except Exception as e:
                    print(f"Error getting file from URI: {e}")

        drag_context.finish(True, False, time)

    def stop_monitoring(self):
        """Остановить мониторинг и очистить временные файлы"""
        # Очистка временных фавиконов
        for cell in self.cells:
            if hasattr(cell, 'favicon_temp_path') and cell.favicon_temp_path:
                if os.path.exists(cell.favicon_temp_path):
                    try:
                        os.remove(cell.favicon_temp_path)
                    except Exception:
                        pass

        # Остановка observer
        self.observer.stop()
        self.observer.join()