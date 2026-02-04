import json
import os
from typing import Optional, Dict, Any, List, Callable

from fabric.hyprland.service import HyprlandEvent
from fabric.hyprland.widgets import HyprlandLanguage as Language
from fabric.hyprland.widgets import HyprlandWorkspaces as Workspaces
from fabric.hyprland.widgets import WorkspaceButton, get_hyprland_connection
from fabric.utils.helpers import exec_shell_command_async
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.datetime import DateTime
from fabric.widgets.label import Label
from fabric.widgets.revealer import Revealer
from gi.repository import Gdk, GLib, Gtk

import config.data as data
import modules.icons as icons
from modules.controls import ControlSmall
from modules.dock import Dock
from modules.metrics import Battery, MetricsSmall, NetworkApplet
from modules.systemprofiles import Systemprofiles
from modules.systemtray import SystemTray
from modules.weather import Weather
from widgets.wayland import WaylandWindow as Window


# Константы
CHINESE_NUMERALS = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "〇"]

TOOLTIP_APPS = f"""Launcher

• Apps: Type to search.

• Calculator [Prefix "="]: Solve a math expression.
e.g. "=2+2"

• Converter [Prefix ";"]: Convert between units.
e.g. ";100 USD to EUR", ";10 km to miles"

• Special Commands [Prefix ":"]:
:update - Open {data.APP_NAME_CAP}'s updater.
:d - Open Dashboard.
:w - Open Wallpapers."""

TOOLTIP_POWER = "Power Menu"
TOOLTIP_TOOLS = "Toolbox"
TOOLTIP_OVERVIEW = "Overview"

THEME_CLASSES = ["pills", "dense", "edge", "edgecenter"]


class Bar(Window):
    """Главная панель приложения для Hyprland"""

    def __init__(self, monitor_id: int = 0, **kwargs):
        self.monitor_id = monitor_id
        self.notch = kwargs.get("notch", None)
        self.component_visibility = data.BAR_COMPONENTS_VISIBILITY
        self.dock_instance: Optional[Dock] = None
        self.integrated_dock_widget: Optional[Box] = None
        self.hidden = False

        super().__init__(
            name="bar",
            layer=2,
            exclusivity="auto",
            visible=True,
            all_visible=True,
            monitor=monitor_id,
        )

        # Инициализация компонентов
        self._setup_positioning()
        self._init_workspaces()
        self._init_buttons()
        self._init_widgets()
        self._init_revealers()
        self._setup_layout()
        self._apply_theme()

        # Финальная настройка
        self.systray._update_visibility()
        self.chinese_numbers()

    def _setup_positioning(self):
        """Настроить позиционирование и отступы панели"""
        anchor_map = {
            "Top": "left top right",
            "Bottom": "left bottom right",
            "Left": "left" if data.CENTERED_BAR else "left top bottom",
            "Right": "right" if data.CENTERED_BAR else "top right bottom",
        }
        self.anchor_var = anchor_map.get(data.BAR_POSITION, "left top right")

        # Определение отступов
        if data.VERTICAL:
            self.margin_var = (
                "-8px -8px -8px -8px" if data.BAR_THEME == "Edge"
                else "-4px -8px -4px -4px"
            )
        else:
            if data.BAR_THEME == "Edge":
                self.margin_var = "-8px -8px -8px -8px"
            else:
                self.margin_var = (
                    "-8px -4px -4px -4px" if data.BAR_POSITION == "Bottom"
                    else "-4px -4px -8px -4px"
                )

        self.set_anchor(self.anchor_var)
        self.set_margin(self.margin_var)

    def _calculate_workspace_range(self) -> range:
        """Вычислить диапазон рабочих столов для текущего монитора"""
        # Monitor 0: workspaces 1-10, Monitor 1: workspaces 11-20, etc.
        start_workspace = self.monitor_id * 10 + 1
        end_workspace = start_workspace + 10
        return range(start_workspace, end_workspace)

    def _create_workspace_buttons(self, workspace_range: range, show_number: bool = False) -> List[WorkspaceButton]:
        """Создать кнопки для рабочих столов"""
        buttons = []
        for i in workspace_range:
            label = None
            if show_number:
                workspace_index = i - workspace_range.start
                label = (
                    CHINESE_NUMERALS[workspace_index]
                    if data.BAR_WORKSPACE_USE_CHINESE_NUMERALS
                    and 0 <= workspace_index < len(CHINESE_NUMERALS)
                    else str(i)
                )

            button = WorkspaceButton(
                h_expand=False,
                v_expand=False,
                h_align="center",
                v_align="center",
                id=i,
                label=label,
                style_classes=["vertical"] if data.VERTICAL else None,
            )
            buttons.append(button)

        return buttons

    def _init_workspaces(self):
        """Инициализировать виджеты рабочих столов"""
        workspace_range = self._calculate_workspace_range()
        buttons_factory = (
            None if data.BAR_HIDE_SPECIAL_WORKSPACE
            else Workspaces.default_buttons_factory
        )
        orientation = "h" if not data.VERTICAL else "v"

        # Рабочие столы без номеров
        self.workspaces = Workspaces(
            name="workspaces",
            invert_scroll=True,
            empty_scroll=True,
            v_align="fill",
            orientation=orientation,
            spacing=8,
            buttons=self._create_workspace_buttons(workspace_range),
            buttons_factory=buttons_factory,
        )

        # Рабочие столы с номерами
        spacing = 0 if not data.BAR_WORKSPACE_USE_CHINESE_NUMERALS else 4
        self.workspaces_num = Workspaces(
            name="workspaces-num",
            invert_scroll=True,
            empty_scroll=True,
            v_align="fill",
            orientation=orientation,
            spacing=spacing,
            buttons=self._create_workspace_buttons(workspace_range, show_number=True),
            buttons_factory=buttons_factory,
        )

        # Контейнер для рабочих столов
        self.ws_container = Box(
            name="workspaces-container",
            children=(
                self.workspaces_num if data.BAR_WORKSPACE_SHOW_NUMBER
                else self.workspaces
            ),
        )

    def _create_bar_button(
        self,
        tooltip: str,
        icon: str,
        callback: Callable,
        name: str = "button-bar"
    ) -> Button:
        """Создать кнопку для панели с общими настройками"""
        button = Button(
            name=name,
            tooltip_markup=tooltip,
            on_clicked=lambda *_: callback(),
            child=Label(name="button-bar-label", markup=icon),
        )
        button.connect("enter_notify_event", self.on_button_enter)
        button.connect("leave_notify_event", self.on_button_leave)
        return button

    def _init_buttons(self):
        """Инициализировать кнопки панели"""
        self.connection = get_hyprland_connection()

        self.button_apps = self._create_bar_button(
            TOOLTIP_APPS, icons.apps, self.search_apps
        )
        self.button_tools = self._create_bar_button(
            TOOLTIP_TOOLS, icons.toolbox, self.tools_menu
        )
        self.button_power = self._create_bar_button(
            TOOLTIP_POWER, icons.shutdown, self.power_menu
        )
        self.button_overview = self._create_bar_button(
            TOOLTIP_OVERVIEW, icons.windows, self.overview
        )

        # Кнопка языка
        self.lang_label = Label(name="lang-label")
        self.language = Button(
            name="language",
            h_align="center",
            v_align="center",
            child=self.lang_label
        )
        self.on_language_switch()
        self.connection.connect("event::activelayout", self.on_language_switch)

    def _init_widgets(self):
        """Инициализировать виджеты панели"""
        self.systray = SystemTray()
        self.weather = Weather()
        self.sysprofiles = Systemprofiles()
        self.network = NetworkApplet()
        self.control = ControlSmall()
        self.metrics = MetricsSmall()
        self.battery = Battery()

        # Дата и время
        time_format_map = {
            True: ("%I:%M %p", "%I\n%M\n%p"),  # 12-часовой формат
            False: ("%H:%M", "%H\n%M"),          # 24-часовой формат
        }
        time_h, time_v = time_format_map[data.DATETIME_12H_FORMAT]

        self.date_time = DateTime(
            name="date-time",
            formatters=[time_h if not data.VERTICAL else time_v],
            h_align="center" if not data.VERTICAL else "fill",
            v_align="center",
            h_expand=True,
            v_expand=True,
            style_classes=["vertical"] if data.VERTICAL else [],
        )

        self.apply_component_props()

    def _create_revealer_box(self, children: List, transition: str, name: str = "bar-revealer") -> Box:
        """Создать Box с Revealer"""
        revealer = Revealer(
            name=name,
            transition_type=transition,
            child_revealed=True,
            child=Box(
                name="bar-revealer-box",
                orientation="h",
                spacing=4,
                children=children if not data.VERTICAL else None,
            ),
        )
        return Box(name="boxed-revealer", children=[revealer])

    def _init_revealers(self):
        """Инициализировать Revealer'ы для панели"""
        self.rev_right = [self.metrics, self.control]
        self.revealer_right = Revealer(
            name="bar-revealer",
            transition_type="slide-left",
            child_revealed=True,
            child=Box(
                name="bar-revealer-box",
                orientation="h",
                spacing=4,
                children=self.rev_right if not data.VERTICAL else None,
            ),
        )
        self.boxed_revealer_right = Box(
            name="boxed-revealer",
            children=[self.revealer_right],
        )

        self.rev_left = [self.weather, self.sysprofiles, self.network]
        self.revealer_left = Revealer(
            name="bar-revealer",
            transition_type="slide-right",
            child_revealed=True,
            child=Box(
                name="bar-revealer-box",
                orientation="h",
                spacing=4,
                children=self.rev_left if not data.VERTICAL else None,
            ),
        )
        self.boxed_revealer_left = Box(
            name="boxed-revealer",
            children=[self.revealer_left],
        )

    def _get_layout_children(self) -> tuple:
        """Получить списки дочерних элементов для разных режимов"""
        # Горизонтальные дети
        h_start = [
            self.button_apps,
            self.ws_container,
            self.button_overview,
            self.boxed_revealer_left,
        ]
        h_end = [
            self.boxed_revealer_right,
            self.battery,
            self.systray,
            self.button_tools,
            self.language,
            self.date_time,
            self.button_power,
        ]

        # Вертикальные дети
        v_start = [
            self.button_apps,
            self.systray,
            self.control,
            self.sysprofiles,
            self.network,
            self.button_tools,
        ]
        v_center = [
            self.button_overview,
            self.ws_container,
            self.weather,
        ]
        v_end = [
            self.battery,
            self.metrics,
            self.language,
            self.date_time,
            self.button_power,
        ]

        return h_start, h_end, v_start, v_center, v_end

    def _should_embed_dock(self) -> bool:
        """Определить, нужно ли встраивать док в панель"""
        return (
            data.BAR_POSITION == "Bottom"
            or (data.PANEL_THEME == "Panel" and data.BAR_POSITION in ["Top", "Bottom"])
        )

    def _setup_layout(self):
        """Настроить layout панели"""
        h_start, h_end, v_start, v_center, v_end = self._get_layout_children()

        # Создание встроенного дока если необходимо
        if self._should_embed_dock() and not data.VERTICAL:
            self.dock_instance = Dock(integrated_mode=True)
            self.integrated_dock_widget = self.dock_instance.wrapper

        # Определение центральных детей
        is_centered_bar = data.VERTICAL and getattr(data, "CENTERED_BAR", False)
        v_all_children = v_start + v_center + v_end

        bar_center_actual_children = None
        if self.integrated_dock_widget is not None:
            bar_center_actual_children = self.integrated_dock_widget
        elif data.VERTICAL:
            bar_center_actual_children = Box(
                orientation=Gtk.Orientation.VERTICAL,
                spacing=4,
                children=v_all_children if is_centered_bar else v_center,
            )

        # Создание контейнеров start и end
        orientation = (
            Gtk.Orientation.HORIZONTAL if not data.VERTICAL
            else Gtk.Orientation.VERTICAL
        )

        start_container = None if is_centered_bar else Box(
            name="start-container",
            spacing=4,
            orientation=orientation,
            children=h_start if not data.VERTICAL else v_start,
        )

        end_container = None if is_centered_bar else Box(
            name="end-container",
            spacing=4,
            orientation=orientation,
            children=h_end if not data.VERTICAL else v_end,
        )

        # Создание основного CenterBox
        self.bar_inner = CenterBox(
            name="bar-inner",
            orientation=orientation,
            h_align="fill",
            v_align="fill",
            start_children=start_container,
            center_children=bar_center_actual_children,
            end_children=end_container,
        )

        self.children = self.bar_inner

    def _get_themed_children(self) -> List:
        """Получить список виджетов для применения темы"""
        themed = [
            self.button_apps,
            self.button_overview,
            self.button_power,
            self.button_tools,
            self.language,
            self.date_time,
            self.ws_container,
            self.weather,
            self.network,
            self.battery,
            self.metrics,
            self.systray,
            self.control,
        ]

        if self.integrated_dock_widget:
            themed.append(self.integrated_dock_widget)

        return themed

    def _apply_theme(self):
        """Применить тему к панели"""
        # Удаление старых классов темы
        for theme_class in THEME_CLASSES:
            self.bar_inner.remove_style_class(theme_class)

        # Определение стиля на основе текущей темы
        theme_map = {
            "Pills": "pills",
            "Dense": "dense",
            "Edge": "edgecenter" if (data.VERTICAL and data.CENTERED_BAR) else "edge",
        }
        self.style = theme_map.get(data.BAR_THEME, "pills")
        self.bar_inner.add_style_class(self.style)

        # Применение стиля к встроенному доку
        if self.integrated_dock_widget and hasattr(self.integrated_dock_widget, "add_style_class"):
            for theme_class in ["pills", "dense", "edge"]:
                style_context = self.integrated_dock_widget.get_style_context()
                if style_context.has_class(theme_class):
                    self.integrated_dock_widget.remove_style_class(theme_class)
            self.integrated_dock_widget.add_style_class(self.style)

        # Применение инвертированных стилей для Dense и Edge тем
        if data.BAR_THEME in ["Dense", "Edge"]:
            themed_children = self._get_themed_children()
            for child in themed_children:
                if hasattr(child, "add_style_class"):
                    child.add_style_class("invert")

        # Применение стилей позиционирования
        position_class_map = {
            "Top": "top",
            "Bottom": "bottom",
            "Left": "left",
            "Right": "right",
        }
        position_class = position_class_map.get(data.BAR_POSITION, "top")
        self.bar_inner.add_style_class(position_class)

        if data.VERTICAL:
            self.bar_inner.add_style_class("vertical")

    def apply_component_props(self):
        """Применить свойства видимости к компонентам"""
        components = self._get_components_dict()
        for component_name, widget in components.items():
            if component_name in self.component_visibility:
                widget.set_visible(self.component_visibility[component_name])

    def _get_components_dict(self) -> Dict[str, Any]:
        """Получить словарь всех компонентов панели"""
        return {
            "button_apps": self.button_apps,
            "systray": self.systray,
            "control": self.control,
            "network": self.network,
            "button_tools": self.button_tools,
            "button_overview": self.button_overview,
            "ws_container": self.ws_container,
            "weather": self.weather,
            "battery": self.battery,
            "metrics": self.metrics,
            "language": self.language,
            "date_time": self.date_time,
            "button_power": self.button_power,
            "sysprofiles": self.sysprofiles,
        }

    def toggle_component_visibility(self, component_name: str) -> Optional[bool]:
        """Переключить видимость компонента"""
        components = self._get_components_dict()

        if component_name not in components or component_name not in self.component_visibility:
            return None

        # Переключение видимости
        self.component_visibility[component_name] = not self.component_visibility[component_name]
        components[component_name].set_visible(self.component_visibility[component_name])

        # Сохранение в конфиг
        self._save_component_visibility(component_name)

        return self.component_visibility[component_name]

    def _save_component_visibility(self, component_name: str):
        """Сохранить видимость компонента в конфигурационный файл"""
        config_file = os.path.expanduser(f"~/.config/{data.APP_NAME}/config/config.json")

        if not os.path.exists(config_file):
            return

        try:
            with open(config_file, "r") as f:
                config = json.load(f)

            config[f"bar_{component_name}_visible"] = self.component_visibility[component_name]

            with open(config_file, "w") as f:
                json.dump(config, f, indent=4)
        except Exception as e:
            print(f"Error updating config file: {e}")

    def on_button_enter(self, widget, event):
        """Обработчик наведения курсора на кнопку"""
        window = widget.get_window()
        if window:
            window.set_cursor(Gdk.Cursor.new_from_name(widget.get_display(), "hand2"))

    def on_button_leave(self, widget, event):
        """Обработчик ухода курсора с кнопки"""
        window = widget.get_window()
        if window:
            window.set_cursor(None)

    def search_apps(self):
        """Открыть лаунчер приложений"""
        if self.notch:
            self.notch.open_notch("launcher")

    def overview(self):
        """Открыть обзор окон"""
        if self.notch:
            self.notch.open_notch("overview")

    def power_menu(self):
        """Открыть меню питания"""
        if self.notch:
            self.notch.open_notch("power")

    def tools_menu(self):
        """Открыть меню инструментов"""
        if self.notch:
            self.notch.open_notch("tools")

    def on_language_switch(self, _=None, event: HyprlandEvent = None):
        """Обработчик переключения языка клавиатуры"""
        try:
            lang_data = (
                event.data[1]
                if event and event.data and len(event.data) > 1
                else Language().get_label()
            )
        except (json.JSONDecodeError, IndexError):
            lang_data = "UNK"

        self.language.set_tooltip_text(lang_data)

        if not data.VERTICAL:
            self.lang_label.set_label(lang_data[:3].upper())
        else:
            self.lang_label.add_style_class("icon")
            self.lang_label.set_markup(icons.keyboard)

    def toggle_hidden(self):
        """Переключить скрытие панели"""
        self.hidden = not self.hidden

        if self.hidden:
            self.bar_inner.add_style_class("hidden")
        else:
            self.bar_inner.remove_style_class("hidden")

        # Поднять notch над панелью когда панель показана
        if self.notch and not self.hidden:
            GLib.idle_add(
                lambda: exec_shell_command_async(
                    "hyprctl dispatch focuswindow class:notch"
                ) if self.notch else None
            )

    def chinese_numbers(self):
        """Применить стили для китайских цифр"""
        if data.BAR_WORKSPACE_USE_CHINESE_NUMERALS:
            self.workspaces_num.add_style_class("chinese")
        else:
            self.workspaces_num.remove_style_class("chinese")
