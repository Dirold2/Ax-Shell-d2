from typing import Optional, Dict, Any, List, Tuple
import json
import logging
import cairo

from fabric.hyprland.widgets import get_hyprland_connection
from fabric.utils import (exec_shell_command, exec_shell_command_async,
                         get_relative_path, idle_add, remove_handler)
from fabric.utils.helpers import get_desktop_applications
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.eventbox import EventBox
from fabric.widgets.image import Image
from fabric.widgets.revealer import Revealer
from gi.repository import Gdk, GLib, Gtk

import config.data as data
from modules.corners import MyCorner
from utils.icon_resolver import IconResolver
from widgets.wayland import WaylandWindow as Window


# Константы
CONFIG_CHECK_INTERVAL = 2000  # ms
OCCLUSION_CHECK_INTERVAL = 500  # ms
HIDE_DELAY = 250  # ms
UPDATE_DOCK_DELAY = 250  # ms

# Размеры
INTEGRATED_ICON_SIZE = 20
DEFAULT_OCCLUSION_OFFSET = 36
HOVER_ACTIVATOR_SIZE = 1

# Пути
DOCK_CONFIG_PATH = "../config/dock.json"

# Отступы для различных позиций бара
BAR_MARGINS = {
    "Top": "-8px 0px 0px 0px",
    "Bottom": "0px 0px 0px 0px",
    "Left": "0px 0px 0px -8px",
    "Right": "0px -8px 0px 0px",
}


class DockOrientation:
    """Конфигурация ориентации и позиционирования dock"""

    def __init__(self, is_horizontal: bool, bar_position: str = "Bottom"):
        self.is_horizontal = is_horizontal
        self.bar_position = bar_position

    @property
    def anchor(self) -> str:
        """Получить anchor для dock"""
        if self.is_horizontal:
            return "bottom"
        return "right" if self.bar_position == "Left" else "left"

    @property
    def revealer_transition(self) -> str:
        """Получить тип перехода для revealer"""
        if self.is_horizontal:
            return "slide-up"
        return "slide-left" if self.bar_position == "Right" else "slide-right"

    @property
    def main_box_orientation(self) -> Gtk.Orientation:
        """Получить ориентацию главного box"""
        return Gtk.Orientation.VERTICAL if self.is_horizontal else Gtk.Orientation.HORIZONTAL

    @property
    def dock_wrapper_orientation(self) -> Gtk.Orientation:
        """Получить ориентацию wrapper"""
        return Gtk.Orientation.HORIZONTAL if self.is_horizontal else Gtk.Orientation.VERTICAL

    @property
    def main_box_h_align(self) -> str:
        """Получить горизонтальное выравнивание главного box"""
        if self.is_horizontal:
            return "center"
        return "end" if self.anchor == "right" else "start"

    @property
    def hover_activator_size(self) -> Tuple[int, int]:
        """Получить размер активатора hover (width, height)"""
        if self.is_horizontal:
            return (-1, HOVER_ACTIVATOR_SIZE)
        return (HOVER_ACTIVATOR_SIZE, -1)


class DockConfig:
    """Управление конфигурацией dock"""

    def __init__(self, config_path: str = DOCK_CONFIG_PATH):
        self.config_path = get_relative_path(config_path)
        self.config = self._read_config()

    def _read_config(self) -> Dict[str, Any]:
        """Прочитать конфигурацию из файла"""
        try:
            with open(self.config_path, "r") as file:
                config_data = json.load(file)

            # Миграция старого формата
            if self._needs_migration(config_data):
                config_data = self._migrate_config(config_data)

            return config_data
        except (FileNotFoundError, json.JSONDecodeError):
            return {"pinned_apps": []}

    def _needs_migration(self, config_data: Dict) -> bool:
        """Проверить нужна ли миграция конфигурации"""
        pinned = config_data.get("pinned_apps", [])
        return pinned and isinstance(pinned[0], str)

    def _migrate_config(self, config_data: Dict) -> Dict:
        """Мигрировать конфигурацию из старого формата"""
        all_apps = get_desktop_applications()
        app_map = {app.name: app for app in all_apps if app.name}
        old_pinned = config_data["pinned_apps"]
        config_data["pinned_apps"] = []

        for app_id in old_pinned:
            app = app_map.get(app_id)
            if app:
                app_data = {
                    "name": app.name,
                    "display_name": app.display_name,
                    "window_class": app.window_class,
                    "executable": app.executable,
                    "command_line": app.command_line
                }
                config_data["pinned_apps"].append(app_data)
            else:
                config_data["pinned_apps"].append({"name": app_id})

        return config_data

    @property
    def pinned_apps(self) -> List:
        """Получить список закрепленных приложений"""
        return self.config.get("pinned_apps", [])

    @pinned_apps.setter
    def pinned_apps(self, value: List):
        """Установить список закрепленных приложений"""
        self.config["pinned_apps"] = value

    def save(self) -> bool:
        """Сохранить конфигурацию в файл"""
        try:
            with open(self.config_path, "w") as file:
                json.dump(self.config, file, indent=4)
            return True
        except Exception as e:
            logging.error(f"Failed to write dock config: {e}")
            return False

    def reload(self):
        """Перезагрузить конфигурацию из файла"""
        self.config = self._read_config()


def create_surface_from_widget(widget: Gtk.Widget) -> cairo.ImageSurface:
    """Создать Cairo surface из GTK виджета для drag-and-drop"""
    alloc = widget.get_allocation()
    surface = cairo.ImageSurface(cairo.Format.ARGB32, alloc.width, alloc.height)
    cr = cairo.Context(surface)
    cr.set_source_rgba(255, 255, 255, 0)
    cr.rectangle(0, 0, alloc.width, alloc.height)
    cr.fill()
    widget.draw(cr)
    return surface


# Для обратной совместимости
def createSurfaceFromWidget(widget: Gtk.Widget) -> cairo.ImageSurface:
    """Deprecated: используйте create_surface_from_widget"""
    return create_surface_from_widget(widget)


def read_config() -> Dict[str, Any]:
    """Прочитать конфигурацию dock (функция для обратной совместимости)"""
    config = DockConfig()
    return config.config

class Dock(Window):
    """Dock для запуска и управления приложениями"""

    _instances: List['Dock'] = []

    def __init__(self, monitor_id: int = 0, integrated_mode: bool = False, **kwargs):
        self.monitor_id = monitor_id
        self.integrated_mode = integrated_mode

        # Инициализация состояния
        self._init_state_variables()

        # Вычисление параметров
        orientation_config = self._calculate_orientation()

        # Инициализация базового окна
        if not integrated_mode:
            super().__init__(
                name="dock-window",
                layer=2,
                anchor=orientation_config.anchor,
                margin="0px 0px 0px 0px",
                exclusivity="auto" if self.always_show else "none",
                monitor=monitor_id,
                **kwargs,
            )
            Dock._instances.append(self)

        # Установка отступов
        if not integrated_mode:
            self._set_margins()

        # Инициализация компонентов
        self._init_connections()
        self._init_icon_resolver()
        self._init_drag_state()

        # Создание UI
        self._create_dock_wrapper(orientation_config)
        self._create_dock_full(orientation_config)
        self._create_main_layout(orientation_config)

        # Настройка drag-and-drop
        self._setup_drag_and_drop()

        # Скрыть embedded dock
        self._handle_visibility()

        # Запуск обновления
        self._start_updates()

    def _init_state_variables(self):
        """Инициализировать переменные состояния"""
        self.icon_size = INTEGRATED_ICON_SIZE if self.integrated_mode else data.DOCK_ICON_SIZE
        self.effective_occlusion_size = DEFAULT_OCCLUSION_OFFSET + self.icon_size
        self.always_show = data.DOCK_ALWAYS_SHOW if not self.integrated_mode else False

        self.hide_id: Optional[int] = None
        self._arranger_handler = None
        self._drag_in_progress = False
        self.is_mouse_over_dock_area = False
        self._prevent_occlusion = False
        self._forced_occlusion = False

    def _calculate_orientation(self) -> DockOrientation:
        """Вычислить ориентацию dock"""
        if self.integrated_mode:
            return DockOrientation(is_horizontal=True, bar_position="Bottom")

        is_horizontal = not data.VERTICAL
        return DockOrientation(is_horizontal=is_horizontal, bar_position=data.BAR_POSITION)

    def _set_margins(self):
        """Установить отступы окна"""
        margin = BAR_MARGINS.get(data.BAR_POSITION, "0px 0px 0px 0px")
        self.set_margin(margin)

    def _init_connections(self):
        """Инициализировать подключения"""
        self.dock_config = DockConfig()
        self.conn = get_hyprland_connection()
        self.pinned = self.dock_config.pinned_apps

    def _init_icon_resolver(self):
        """Инициализировать разрешение иконок"""
        self.icon_resolver = IconResolver()
        self._all_apps = get_desktop_applications()
        self.app_identifiers = self._build_app_identifiers_map()
        self.app_map = {app.name: app for app in self._all_apps if app.name}

    def _init_drag_state(self):
        """Инициализировать состояние drag-and-drop"""
        self._drag_in_progress = False

    def _create_dock_wrapper(self, orientation: DockOrientation):
        """Создать wrapper для dock"""
        self.view = Box(name="viewport", spacing=4)
        self.view.set_orientation(orientation.dock_wrapper_orientation)

        style_classes = []
        if data.BAR_POSITION == "Right":
            style_classes.append("left")

        self.wrapper = Box(name="dock", children=[self.view], style_classes=style_classes)
        self.wrapper.set_orientation(orientation.dock_wrapper_orientation)

        # Применение стилей
        self._apply_wrapper_styles(orientation)

    def _apply_wrapper_styles(self, orientation: DockOrientation):
        """Применить стили к wrapper"""
        if self.integrated_mode:
            self.wrapper.add_style_class("integrated")
        else:
            if orientation.dock_wrapper_orientation == Gtk.Orientation.VERTICAL:
                self.wrapper.add_style_class("vertical")

        # Применить тему
        theme_classes = {
            "Pills": "pills",
            "Dense": "dense",
            "Edge": "edge",
        }
        theme_class = theme_classes.get(data.DOCK_THEME, "pills")
        self.wrapper.add_style_class(theme_class)

    def _create_dock_full(self, orientation: DockOrientation):
        """Создать полный dock с углами"""
        if not self.integrated_mode:
            self.dock_eventbox = EventBox()
            self.dock_eventbox.add(self.wrapper)
            self.dock_eventbox.connect("enter-notify-event", self._on_dock_enter)
            self.dock_eventbox.connect("leave-notify-event", self._on_dock_leave)

            # Создать углы
            self._create_corners(orientation)

            # Создать dock_full
            if orientation.is_horizontal:
                self.dock_full = Box(
                    name="dock-full",
                    orientation=Gtk.Orientation.HORIZONTAL,
                    h_expand=True,
                    h_align="fill",
                    children=[self.corner_left, self.dock_eventbox, self.corner_right]
                )
            else:
                self.dock_full = Box(
                    name="dock-full",
                    orientation=Gtk.Orientation.VERTICAL,
                    v_expand=True,
                    v_align="fill",
                    children=[self.corner_top, self.dock_eventbox, self.corner_bottom]
                )

            # Скрыть углы для Edge/Dense тем
            if data.DOCK_THEME in ["Edge", "Dense"]:
                for corner in [self.corner_left, self.corner_right, self.corner_top, self.corner_bottom]:
                    corner.set_visible(False)

    def _create_corners(self, orientation: DockOrientation):
        """Создать углы dock"""
        self.corner_left = Box()
        self.corner_right = Box()
        self.corner_top = Box()
        self.corner_bottom = Box()

        if orientation.is_horizontal:
            self._create_horizontal_corners()
        else:
            self._create_vertical_corners(orientation.anchor)

    def _create_horizontal_corners(self):
        """Создать углы для горизонтального dock"""
        self.corner_left = Box(
            name="dock-corner-left",
            orientation=Gtk.Orientation.VERTICAL,
            h_align="start",
            children=[Box(v_expand=True, v_align="fill"), MyCorner("bottom-right")]
        )
        self.corner_right = Box(
            name="dock-corner-right",
            orientation=Gtk.Orientation.VERTICAL,
            h_align="end",
            children=[Box(v_expand=True, v_align="fill"), MyCorner("bottom-left")]
        )

    def _create_vertical_corners(self, anchor: str):
        """Создать углы для вертикального dock"""
        if anchor == "right":
            self.corner_top = Box(
                name="dock-corner-top",
                orientation=Gtk.Orientation.HORIZONTAL,
                v_align="start",
                children=[Box(h_expand=True, h_align="fill"), MyCorner("bottom-right")]
            )
            self.corner_bottom = Box(
                name="dock-corner-bottom",
                orientation=Gtk.Orientation.HORIZONTAL,
                v_align="end",
                children=[Box(h_expand=True, h_align="fill"), MyCorner("top-right")]
            )
        else:
            self.corner_top = Box(
                name="dock-corner-top",
                orientation=Gtk.Orientation.HORIZONTAL,
                v_align="start",
                children=[MyCorner("bottom-left"), Box(h_expand=True, h_align="fill")]
            )
            self.corner_bottom = Box(
                name="dock-corner-bottom",
                orientation=Gtk.Orientation.HORIZONTAL,
                v_align="end",
                children=[MyCorner("top-left"), Box(h_expand=True, h_align="fill")]
            )

    def _create_main_layout(self, orientation: DockOrientation):
        """Создать главный layout"""
        if not self.integrated_mode:
            self.dock_revealer = Revealer(
                name="dock-revealer",
                transition_type=orientation.revealer_transition,
                transition_duration=250,
                child_revealed=False,
                child=self.dock_full
            )

            # Hover activator
            self.hover_activator = EventBox()
            width, height = orientation.hover_activator_size
            self.hover_activator.set_size_request(width, height)
            self.hover_activator.connect("enter-notify-event", self._on_hover_enter)
            self.hover_activator.connect("leave-notify-event", self._on_hover_leave)

            # Main box
            children = [self.hover_activator, self.dock_revealer]
            if data.BAR_POSITION == "Right":
                children = [self.dock_revealer, self.hover_activator]

            self.main_box = Box(
                orientation=orientation.main_box_orientation,
                children=children,
                h_align=orientation.main_box_h_align,
            )

            self.add(self.main_box)

    def _setup_drag_and_drop(self):
        """Настроить drag-and-drop"""
        target_entry = Gtk.TargetEntry.new("text/plain", Gtk.TargetFlags.SAME_APP, 0)

        # Source
        self.view.drag_source_set(
            Gdk.ModifierType.BUTTON1_MASK,
            [target_entry],
            Gdk.DragAction.MOVE
        )

        # Destination
        self.view.drag_dest_set(
            Gtk.DestDefaults.ALL,
            [target_entry],
            Gdk.DragAction.MOVE
        )

        # Connect signals
        self.view.connect("drag-data-get", self.on_drag_data_get)
        self.view.connect("drag-data-received", self.on_drag_data_received)
        self.view.connect("drag-begin", self.on_drag_begin)
        self.view.connect("drag-end", self.on_drag_end)

    def _handle_visibility(self):
        """Обработать видимость dock"""
        should_be_embedded = (
            (data.BAR_POSITION == "Bottom") or
            (data.PANEL_THEME == "Panel" and data.BAR_POSITION in ["Top", "Bottom"])
        )

        if should_be_embedded or not data.DOCK_ENABLED:
            self.set_visible(False)

        if not self.integrated_mode and self.always_show:
            self.dock_full.add_style_class("occluded")

    def _start_updates(self):
        """Запустить обновления и мониторинг"""
        if self.conn.ready:
            self.update_dock()
            if not self.integrated_mode:
                GLib.timeout_add(OCCLUSION_CHECK_INTERVAL, self.check_occlusion_state)
        else:
            self.conn.connect("event::ready", self.update_dock)
            if not self.integrated_mode:
                self.conn.connect(
                    "event::ready",
                    lambda *args: GLib.timeout_add(UPDATE_DOCK_DELAY, self.check_occlusion_state)
                )

        # Подписаться на события окон
        self.conn.connect("event::openwindow", self.update_dock)
        self.conn.connect("event::closewindow", self.update_dock)

        if not self.integrated_mode:
            self.conn.connect("event::workspace", self.check_hide)

        # Проверка изменений конфигурации
        GLib.timeout_add_seconds(2, self.check_config_change)

    # ==================== App Identifiers ====================

    def _build_app_identifiers_map(self) -> Dict[str, Any]:
        """Построить карту идентификаторов приложений"""
        identifiers = {}
        for app in self._all_apps:
            if app.name:
                identifiers[app.name.lower()] = app
            if app.display_name:
                identifiers[app.display_name.lower()] = app
            if app.window_class:
                identifiers[app.window_class.lower()] = app
            if app.executable:
                identifiers[app.executable.split('/')[-1].lower()] = app
            if app.command_line:
                cmd_base = app.command_line.split()[0].split('/')[-1].lower()
                identifiers[cmd_base] = app
        return identifiers

    def _normalize_window_class(self, class_name: str) -> str:
        """Нормализовать класс окна"""
        if not class_name:
            return ""

        normalized = class_name.lower()
        suffixes = [".bin", ".exe", ".so", "-bin", "-gtk"]

        for suffix in suffixes:
            if normalized.endswith(suffix):
                normalized = normalized[:-len(suffix)]

        return normalized

    def _classes_match(self, class1: str, class2: str) -> bool:
        """Проверить совпадение классов окон"""
        if not class1 or not class2:
            return False

        norm1 = self._normalize_window_class(class1)
        norm2 = self._normalize_window_class(class2)
        return norm1 == norm2

    def update_app_map(self):
        """Обновить карту приложений"""
        self._all_apps = get_desktop_applications()
        self.app_map = {app.name: app for app in self._all_apps if app.name}
        self.app_identifiers = self._build_app_identifiers_map()

    def find_app(self, app_identifier) -> Optional[Any]:
        """Найти приложение по идентификатору"""
        if not app_identifier:
            return None

        if isinstance(app_identifier, dict):
            for key in ["window_class", "executable", "command_line", "name", "display_name"]:
                if key in app_identifier and app_identifier[key]:
                    app = self.find_app_by_key(app_identifier[key])
                    if app:
                        return app
            return None

        return self.find_app_by_key(app_identifier)

    def find_app_by_key(self, key_value: str) -> Optional[Any]:
        """Найти приложение по ключу"""
        if not key_value:
            return None

        normalized_id = str(key_value).lower()

        # Exact match
        if normalized_id in self.app_identifiers:
            return self.app_identifiers[normalized_id]

        # Fuzzy match
        for app in self._all_apps:
            if app.name and normalized_id in app.name.lower():
                return app
            if app.display_name and normalized_id in app.display_name.lower():
                return app
            if app.window_class and normalized_id in app.window_class.lower():
                return app
            if app.executable and normalized_id in app.executable.lower():
                return app
            if app.command_line and normalized_id in app.command_line.lower():
                return app

        return None

    # ==================== Button Creation ====================

    def create_button(self, app_identifier, instances: List) -> Button:
        """Создать кнопку приложения"""
        desktop_app = self.find_app(app_identifier)

        # Получить иконку и имя
        icon_pixbuf = self._get_app_icon(app_identifier, desktop_app)
        display_name = self._get_display_name(app_identifier, desktop_app, instances)

        # Создать кнопку
        button = Button(
            child=Box(
                name="dock-icon",
                orientation="v",
                h_align="center",
                children=[Image(pixbuf=icon_pixbuf)]
            ),
            on_clicked=lambda *a: self.handle_app(app_identifier, instances, desktop_app),
            tooltip_text=display_name,
            name="dock-app-button",
        )

        # Установить атрибуты
        button.app_identifier = app_identifier
        button.desktop_app = desktop_app
        button.instances = instances

        # Добавить стиль если есть экземпляры
        if instances:
            button.add_style_class("instance")

        # Настроить drag-and-drop
        self._setup_button_drag(button)

        return button

    def _get_app_icon(self, app_identifier, desktop_app) -> Any:
        """Получить иконку приложения"""
        # Попытка через desktop app
        if desktop_app:
            icon_pixbuf = desktop_app.get_icon_pixbuf(size=self.icon_size)
            if icon_pixbuf:
                return icon_pixbuf

        # Попытка через icon resolver
        id_value = app_identifier["name"] if isinstance(app_identifier, dict) else app_identifier
        icon_pixbuf = self.icon_resolver.get_icon_pixbuf(id_value, self.icon_size)
        if icon_pixbuf:
            return icon_pixbuf

        # Fallback иконки
        for fallback in ["application-x-executable-symbolic", "image-missing"]:
            icon_pixbuf = self.icon_resolver.get_icon_pixbuf(fallback, self.icon_size)
            if icon_pixbuf:
                return icon_pixbuf

        return None

    def _get_display_name(self, app_identifier, desktop_app, instances: List) -> str:
        """Получить отображаемое имя приложения"""
        if desktop_app and (desktop_app.display_name or desktop_app.name):
            return desktop_app.display_name or desktop_app.name

        if isinstance(app_identifier, dict):
            id_value = app_identifier.get("name", "Unknown")
        else:
            id_value = app_identifier if isinstance(app_identifier, str) else "Unknown"

        # Использовать заголовок окна если нет имени
        if not desktop_app and instances and instances[0].get("title"):
            return instances[0]["title"]

        return id_value

    def _setup_button_drag(self, button: Button):
        """Настроить drag-and-drop для кнопки"""
        target_entry = Gtk.TargetEntry.new("text/plain", Gtk.TargetFlags.SAME_APP, 0)

        button.drag_source_set(
            Gdk.ModifierType.BUTTON1_MASK,
            [target_entry],
            Gdk.DragAction.MOVE
        )

        button.drag_dest_set(
            Gtk.DestDefaults.ALL,
            [target_entry],
            Gdk.DragAction.MOVE
        )

        button.connect("drag-begin", self.on_drag_begin)
        button.connect("drag-end", self.on_drag_end)
        button.connect("drag-data-get", self.on_drag_data_get)
        button.connect("drag-data-received", self.on_drag_data_received)
        button.connect("enter-notify-event", self._on_child_enter)

    # ==================== App Handling ====================

    def handle_app(self, app_identifier, instances: List, desktop_app=None):
        """Обработать клик по приложению"""
        if not instances:
            self._launch_app(app_identifier, desktop_app)
        else:
            self._focus_next_instance(instances)

    def _launch_app(self, app_identifier, desktop_app):
        """Запустить приложение"""
        if not desktop_app:
            desktop_app = self.find_app(app_identifier)

        if desktop_app:
            launch_success = desktop_app.launch()
            if not launch_success:
                self._fallback_launch(desktop_app)
        else:
            self._launch_by_identifier(app_identifier)

    def _fallback_launch(self, desktop_app):
        """Запуск через command line/executable"""
        if desktop_app.command_line:
            exec_shell_command_async(f"nohup {desktop_app.command_line} &")
        elif desktop_app.executable:
            exec_shell_command_async(f"nohup {desktop_app.executable} &")

    def _launch_by_identifier(self, app_identifier):
        """Запустить приложение по идентификатору"""
        cmd_to_run = None

        if isinstance(app_identifier, dict):
            for key in ["command_line", "executable", "name"]:
                if key in app_identifier and app_identifier[key]:
                    cmd_to_run = app_identifier[key]
                    break
        elif isinstance(app_identifier, str):
            cmd_to_run = app_identifier

        if cmd_to_run:
            exec_shell_command_async(f"nohup {cmd_to_run} &")

    def _focus_next_instance(self, instances: List):
        """Переключиться на следующий экземпляр приложения"""
        focused = self.get_focused()
        idx = next((i for i, inst in enumerate(instances) if inst["address"] == focused), -1)
        next_inst = instances[(idx + 1) % len(instances)]
        exec_shell_command(f"hyprctl dispatch focuswindow address:{next_inst['address']}")

    # ==================== Update Dock ====================

    def update_dock(self, *args):
        """Обновить dock"""
        self.update_app_map()

        # Удалить старый handler если есть
        if self._arranger_handler:
            remove_handler(self._arranger_handler)

        clients = self.get_clients()
        running_windows = self._build_running_windows(clients)

        # Создать кнопки для закрепленных приложений
        pinned_buttons, used_classes = self._create_pinned_buttons(running_windows)

        # Создать кнопки для открытых приложений
        open_buttons = self._create_open_buttons(running_windows, used_classes)

        # Объединить с разделителем
        children = self._combine_buttons(pinned_buttons, open_buttons)

        self.view.children = children

        if not self.integrated_mode:
            idle_add(self._update_size)

        self._drag_in_progress = False

        if not self.integrated_mode:
            self.check_occlusion_state()

    def _build_running_windows(self, clients: List) -> Dict[str, List]:
        """Построить словарь запущенных окон"""
        running_windows = {}

        for c in clients:
            window_id = self._extract_window_id(c)
            running_windows.setdefault(window_id, []).append(c)

            # Добавить нормализованную версию
            normalized_id = self._normalize_window_class(window_id)
            if normalized_id != window_id:
                running_windows.setdefault(normalized_id, []).extend(running_windows[window_id])

        return running_windows

    def _extract_window_id(self, client: Dict) -> str:
        """Извлечь идентификатор окна из данных клиента"""
        # Попытка через class
        if class_name := client.get("initialClass", "").lower():
            return class_name
        if class_name := client.get("class", "").lower():
            return class_name

        # Попытка через title
        if title := client.get("title", "").lower():
            possible_name = title.split(" - ")[0].strip()
            if possible_name and len(possible_name) > 1:
                return possible_name
            return title

        return "unknown-app"

    def _create_pinned_buttons(self, running_windows: Dict) -> Tuple[List[Button], set]:
        """Создать кнопки для закрепленных приложений"""
        pinned_buttons = []
        used_classes = set()

        for app_data in self.pinned:
            instances, matched_class = self._find_instances_for_app(app_data, running_windows)

            if matched_class:
                used_classes.add(matched_class)
                used_classes.add(self._normalize_window_class(matched_class))

            pinned_buttons.append(self.create_button(app_data, instances))

        return pinned_buttons, used_classes

    def _find_instances_for_app(self, app_data, running_windows: Dict) -> Tuple[List, Optional[str]]:
        """Найти экземпляры для приложения"""
        app = self.find_app(app_data)
        possible_identifiers = self._get_possible_identifiers(app_data, app)

        for identifier in possible_identifiers:
            # Exact match
            if identifier in running_windows:
                return running_windows[identifier], identifier

            # Normalized match
            normalized = self._normalize_window_class(identifier)
            if normalized in running_windows:
                return running_windows[normalized], normalized

            # Fuzzy match
            if len(identifier) >= 3:
                for window_class in running_windows:
                    if identifier in window_class:
                        return running_windows[window_class], window_class

        return [], None

    def _get_possible_identifiers(self, app_data, app) -> List[str]:
        """Получить возможные идентификаторы для приложения"""
        identifiers = []

        # Из app_data
        if isinstance(app_data, dict):
            for key in ["window_class", "executable", "command_line", "name", "display_name"]:
                if key in app_data and app_data[key]:
                    identifiers.append(app_data[key].lower())
        elif isinstance(app_data, str):
            identifiers.append(app_data.lower())

        # Из desktop app
        if app:
            if app.window_class:
                identifiers.append(app.window_class.lower())
            if app.executable:
                identifiers.append(app.executable.split('/')[-1].lower())
            if app.command_line:
                cmd_parts = app.command_line.split()
                if cmd_parts:
                    identifiers.append(cmd_parts[0].split('/')[-1].lower())
            if app.name:
                identifiers.append(app.name.lower())
            if app.display_name:
                identifiers.append(app.display_name.lower())

        return list(set(identifiers))

    def _create_open_buttons(self, running_windows: Dict, used_classes: set) -> List[Button]:
        """Создать кнопки для открытых приложений"""
        open_buttons = []

        for class_name, instances in running_windows.items():
            if class_name not in used_classes:
                identifier = self._create_app_identifier(class_name, instances)
                open_buttons.append(self.create_button(identifier, instances))

        return open_buttons

    def _create_app_identifier(self, class_name: str, instances: List):
        """Создать идентификатор приложения для открытого окна"""
        app = self.app_identifiers.get(class_name)

        if not app:
            norm_class = self._normalize_window_class(class_name)
            app = self.app_identifiers.get(norm_class)

        if not app:
            app = self.find_app_by_key(class_name)

        # Попытка по заголовку
        if not app and instances and instances[0].get("title"):
            title = instances[0].get("title", "")
            potential_name = title.split(" - ")[0].strip()
            if len(potential_name) > 2:
                app = self.find_app_by_key(potential_name)

        if app:
            return {
                "name": app.name,
                "display_name": app.display_name,
                "window_class": app.window_class,
                "executable": app.executable,
                "command_line": app.command_line
            }

        return class_name

    def _combine_buttons(self, pinned_buttons: List, open_buttons: List) -> List:
        """Объединить кнопки с разделителем"""
        children = pinned_buttons.copy()

        if pinned_buttons and open_buttons:
            separator_orientation = (
                Gtk.Orientation.VERTICAL if self.view.get_orientation() == Gtk.Orientation.HORIZONTAL
                else Gtk.Orientation.HORIZONTAL
            )
            separator = Box(
                orientation=separator_orientation,
                v_expand=False,
                h_expand=False,
                h_align="center",
                v_align="center",
                name="dock-separator"
            )
            children.append(separator)

        children.extend(open_buttons)
        return children

    def _update_size(self) -> bool:
        """Обновить размер dock"""
        if self.integrated_mode:
            return False

        width, _ = self.view.get_preferred_width()
        self.set_size_request(width, -1)
        return False

    # ==================== Hover Events ====================

    def _on_hover_enter(self, *args) -> bool:
        """Обработать вход мыши в область активации"""
        if self.integrated_mode:
            return False

        self.is_mouse_over_dock_area = True

        if self.hide_id:
            GLib.source_remove(self.hide_id)
            self.hide_id = None

        self.dock_revealer.set_reveal_child(True)
        if not self.always_show:
            self.dock_full.remove_style_class("occluded")

        return False

    def _on_hover_leave(self, *args) -> bool:
        """Обработать выход мыши из области активации"""
        if self.integrated_mode:
            return False

        self.is_mouse_over_dock_area = False

        if self._forced_occlusion:
            self.dock_revealer.set_reveal_child(False)
        else:
            self.delay_hide()

        return False

    def _on_dock_enter(self, widget, event) -> bool:
        """Обработать вход мыши в dock"""
        if self.integrated_mode:
            return True

        self.is_mouse_over_dock_area = True

        if self.hide_id:
            GLib.source_remove(self.hide_id)
            self.hide_id = None

        self.dock_revealer.set_reveal_child(True)
        if not self.always_show:
            self.dock_full.remove_style_class("occluded")

        return True

    def _on_dock_leave(self, widget, event) -> bool:
        """Обработать выход мыши из dock"""
        if self.integrated_mode:
            return True

        if event.detail == Gdk.NotifyType.INFERIOR:
            return False

        self.is_mouse_over_dock_area = False

        if self._forced_occlusion:
            self.dock_revealer.set_reveal_child(False)
        else:
            self.delay_hide()

        if not self.always_show:
            self.dock_full.add_style_class("occluded")

        return True

    def _on_child_enter(self, widget, event) -> bool:
        """Обработать вход мыши в дочерний элемент"""
        if self.integrated_mode:
            return False

        self.is_mouse_over_dock_area = True

        if self.hide_id:
            GLib.source_remove(self.hide_id)
            self.hide_id = None

        return False

    def delay_hide(self):
        """Отложить скрытие dock"""
        if self.integrated_mode:
            return

        if self.hide_id:
            GLib.source_remove(self.hide_id)

        self.hide_id = GLib.timeout_add(HIDE_DELAY, self.hide_dock_if_not_hovered)

    def hide_dock_if_not_hovered(self) -> bool:
        """Скрыть dock если мышь не над ним"""
        if self.integrated_mode:
            return False

        self.hide_id = None

        if not self.is_mouse_over_dock_area and not self._drag_in_progress and not self._prevent_occlusion:
            if not self.always_show:
                self.dock_revealer.set_reveal_child(False)

        return False

    # ==================== Drag and Drop ====================

    def on_drag_begin(self, widget, drag_context):
        """Обработать начало drag"""
        self._drag_in_progress = True
        Gtk.drag_set_icon_surface(drag_context, create_surface_from_widget(widget))

    def on_drag_end(self, widget, drag_context):
        """Обработать окончание drag"""
        if not self._drag_in_progress:
            return

        def process_drag_end():
            display = Gdk.Display.get_default()
            _, x, y, _ = display.get_pointer()

            # Проверить находится ли указатель вне dock
            alloc = self.view.get_allocation()
            is_outside = not (
                alloc.x <= x <= alloc.x + alloc.width and
                alloc.y <= y <= alloc.y + alloc.height
            )

            if is_outside:
                self._handle_drag_outside(widget)

            self._drag_in_progress = False

            if not self.integrated_mode:
                self.check_occlusion_state()

        GLib.idle_add(process_drag_end)

    def _handle_drag_outside(self, widget):
        """Обработать drag за пределы dock"""
        app_id = widget.app_identifier
        instances = widget.instances

        # Открепить приложение
        app_index = self._find_pinned_app_index(app_id)

        if app_index >= 0:
            self.pinned.pop(app_index)
            self.dock_config.pinned_apps = self.pinned
            self.dock_config.save()
            self.update_dock()
        elif instances:
            # Фокусировать окно если есть экземпляры
            address = instances[0].get("address")
            if address:
                exec_shell_command(f"hyprctl dispatch focuswindow address:{address}")

    def _find_pinned_app_index(self, app_id) -> int:
        """Найти индекс закрепленного приложения"""
        for i, pinned_app in enumerate(self.pinned):
            if self._app_ids_match(app_id, pinned_app):
                return i
        return -1

    def _app_ids_match(self, app_id1, app_id2) -> bool:
        """Проверить совпадают ли идентификаторы приложений"""
        if isinstance(app_id1, dict) and isinstance(app_id2, dict):
            return app_id1.get("name") == app_id2.get("name")
        return app_id1 == app_id2

    def _find_drag_target(self, widget) -> Optional[Any]:
        """Найти целевой виджет для drag"""
        children = self.view.get_children()
        while widget is not None and widget not in children:
            widget = widget.get_parent() if hasattr(widget, "get_parent") else None
        return widget

    def on_drag_data_get(self, widget, drag_context, data_obj, info, time):
        """Получить данные для drag"""
        target = self._find_drag_target(
            widget.get_parent() if isinstance(widget, Box) else widget
        )

        if target is not None:
            index = self.view.get_children().index(target)
            data_obj.set_text(str(index), -1)

    def on_drag_data_received(self, widget, drag_context, x, y, data_obj, info, time):
        """Получить данные при drop"""
        target = self._find_drag_target(
            widget.get_parent() if isinstance(widget, Box) else widget
        )

        if target is None:
            return

        try:
            source_index = int(data_obj.get_text())
        except (TypeError, ValueError):
            return

        children = self.view.get_children()

        try:
            target_index = children.index(target)
        except ValueError:
            return

        if source_index == target_index:
            return

        # Проверить пересекает ли drag разделитель
        separator_index = self._find_separator_index(children)
        cross_section = self._is_cross_section_drag(
            source_index, target_index, separator_index
        )

        # Переместить элемент
        child_to_move = children.pop(source_index)
        children.insert(target_index, child_to_move)
        self.view.children = children

        # Обновить закрепленные приложения
        self.update_pinned_apps(skip_update=not cross_section)

        if cross_section:
            GLib.idle_add(self.update_dock)

    def _find_separator_index(self, children: List) -> int:
        """Найти индекс разделителя"""
        for i, child in enumerate(children):
            if child.get_name() == "dock-separator":
                return i
        return -1

    def _is_cross_section_drag(self, source: int, target: int, separator: int) -> bool:
        """Проверить пересекает ли drag разделитель"""
        if separator == -1:
            return False
        return (
            (source < separator and target > separator) or
            (source > separator and target < separator)
        )

    # ==================== Pinned Apps Management ====================

    def update_pinned_apps(self, skip_update: bool = False):
        """Обновить список закрепленных приложений"""
        pinned_data = []

        for child in self.view.get_children():
            if child.get_name() == "dock-separator":
                break

            if hasattr(child, "app_identifier"):
                if hasattr(child, "desktop_app") and child.desktop_app:
                    app = child.desktop_app
                    app_data = {
                        "name": app.name,
                        "display_name": app.display_name,
                        "window_class": app.window_class,
                        "executable": app.executable,
                        "command_line": app.command_line
                    }
                    pinned_data.append(app_data)
                else:
                    pinned_data.append(child.app_identifier)

        self.dock_config.pinned_apps = pinned_data
        self.pinned = pinned_data

        file_updated = self.dock_config.save()

        if file_updated and not skip_update:
            self.update_dock()

    # ==================== Occlusion and Visibility ====================

    def check_occlusion_state(self) -> bool:
        """Проверить состояние окклюзии"""
        if self.integrated_mode:
            return False

        # Forced occlusion - показывать только при hover
        if self._forced_occlusion:
            if self.is_mouse_over_dock_area:
                if not self.dock_revealer.get_reveal_child():
                    self.dock_revealer.set_reveal_child(True)
                    self.dock_full.remove_style_class("occluded")
            else:
                if self.dock_revealer.get_reveal_child():
                    self.dock_revealer.set_reveal_child(False)
                    self.dock_full.add_style_class("occluded")
            return True

        # Не скрывать если мышь над dock или drag в процессе
        if self.is_mouse_over_dock_area or self._drag_in_progress or self._prevent_occlusion:
            if not self.dock_revealer.get_reveal_child():
                self.dock_revealer.set_reveal_child(True)
                if not self.always_show:
                    self.dock_full.remove_style_class("occluded")
            return True

        # Always show mode
        if self.always_show:
            if not self.dock_revealer.get_reveal_child():
                self.dock_revealer.set_reveal_child(True)
                self.dock_full.remove_style_class("occluded")
        else:
            if self.dock_revealer.get_reveal_child():
                self.dock_revealer.set_reveal_child(False)
                self.dock_full.add_style_class("occluded")

        return True

    def force_occlusion(self):
        """Принудительно скрыть dock"""
        if self.integrated_mode:
            return

        self._saved_always_show = self.always_show
        self.always_show = False
        self._forced_occlusion = True

        if not self.is_mouse_over_dock_area:
            self.dock_revealer.set_reveal_child(False)

    def restore_from_occlusion(self):
        """Восстановить dock из режима окклюзии"""
        if self.integrated_mode:
            return

        self._forced_occlusion = False

        if hasattr(self, '_saved_always_show'):
            self.always_show = self._saved_always_show
            delattr(self, '_saved_always_show')

        self.check_occlusion_state()

    def check_hide(self, *args):
        """Проверить нужно ли скрыть dock"""
        if self.integrated_mode:
            return

        if self.is_mouse_over_dock_area or self._drag_in_progress or self._prevent_occlusion:
            return

        clients = self.get_clients()
        current_ws = self.get_workspace()
        ws_clients = [w for w in clients if w["workspace"]["id"] == current_ws]

        if self.always_show:
            if not self.dock_revealer.get_reveal_child():
                self.dock_revealer.set_reveal_child(True)
                self.dock_full.remove_style_class("occluded")
        else:
            if self.dock_revealer.get_reveal_child():
                self.dock_revealer.set_reveal_child(False)
                self.dock_full.add_style_class("occluded")

    # ==================== Hyprland Queries ====================

    def get_clients(self) -> List[Dict]:
        """Получить список клиентов Hyprland"""
        try:
            reply = self.conn.send_command("j/clients").reply.decode()
            return json.loads(reply)
        except json.JSONDecodeError:
            return []

    def get_focused(self) -> str:
        """Получить адрес сфокусированного окна"""
        try:
            reply = self.conn.send_command("j/activewindow").reply.decode()
            return json.loads(reply).get("address", "")
        except json.JSONDecodeError:
            return ""

    def get_workspace(self) -> int:
        """Получить ID активного workspace"""
        try:
            reply = self.conn.send_command("j/activeworkspace").reply.decode()
            return json.loads(reply).get("id", 0)
        except json.JSONDecodeError:
            return 0

    # ==================== Config Management ====================

    def check_config_change(self) -> bool:
        """Проверить изменения в конфигурации"""
        self.dock_config.reload()

        if not self.integrated_mode:
            new_always_show = data.DOCK_ALWAYS_SHOW
            if self.always_show != new_always_show:
                self.always_show = new_always_show
                self.check_occlusion_state()

        if self.dock_config.pinned_apps != self.pinned:
            self.pinned = self.dock_config.pinned_apps
            self.update_app_map()
            self.update_dock()

        return True

    def check_config_change_immediate(self) -> bool:
        """Немедленно проверить изменения конфигурации"""
        self.dock_config.reload()

        if not self.integrated_mode:
            previous_always_show = self.always_show
            self.always_show = data.DOCK_ALWAYS_SHOW
            if previous_always_show != self.always_show:
                self.check_occlusion_state()

        if self.dock_config.pinned_apps != self.pinned:
            self.pinned = self.dock_config.pinned_apps
            self.update_app_map()
            self.update_dock()

        return False

    # ==================== Static Methods ====================

    @staticmethod
    def notify_config_change():
        """Уведомить все экземпляры об изменении конфигурации"""
        for dock_instance in Dock._instances:
            GLib.idle_add(dock_instance.check_config_change_immediate)

    @staticmethod
    def update_visibility(visible: bool):
        """Обновить видимость всех dock экземпляров"""
        for dock in Dock._instances:
            dock.set_visible(visible)
            if visible:
                GLib.idle_add(dock.check_occlusion_state)
            else:
                if hasattr(dock, 'dock_revealer') and dock.dock_revealer.get_reveal_child():
                    dock.dock_revealer.set_reveal_child(False)
