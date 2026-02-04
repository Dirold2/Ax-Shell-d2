from typing import Optional, Dict, Any, Callable, Tuple
import json
import subprocess

from fabric.hyprland.widgets import HyprlandActiveWindow as ActiveWindow
from fabric.utils.helpers import FormattedString, get_desktop_applications
from fabric.widgets.box import Box
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.image import Image
from fabric.widgets.label import Label
from fabric.widgets.revealer import Revealer
from fabric.widgets.stack import Stack
from fabric.audio.service import Audio
from gi.repository import Gdk, GLib, Gtk, Pango
from loguru import logger

import config.data as data
from modules.cliphist import ClipHistory
from modules.corners import MyCorner
from modules.dashboard import Dashboard
from modules.emoji import EmojiPicker
from modules.launcher import AppLauncher
from modules.overview import Overview
from modules.player import PlayerSmall
from modules.power import PowerMenu
from modules.tmux import TmuxManager
from modules.tools import Toolbox
from utils.icon_resolver import IconResolver
from utils.occlusion import check_occlusion
from widgets.wayland import WaylandWindow as Window


# Константы
VOLUME_DISPLAY_DURATION = 2000  # ms
OCCLUSION_CHECK_INTERVAL = 500  # ms
OCCLUSION_RESTORE_DELAY = 500  # ms
AUDIO_CONNECTION_RETRY_INTERVAL = 1000  # ms
MAX_AUDIO_CONNECTION_RETRIES = 5
AUDIO_DISPLAY_ENABLE_DELAY = 500  # ms
LAUNCHER_TRANSITION_DELAY = 150  # ms
SCROLL_DEBOUNCE_DELAY = 500  # ms

# Размеры
COMPACT_SIZE_VERTICAL = (260, 40)
LAUNCHER_SIZE_VERTICAL = (320, 635)
DASHBOARD_SIZE_VERTICAL = (410, 900)
COMPACT_SIZE_HORIZONTAL = (260, 40)
LAUNCHER_SIZE_HORIZONTAL = (480, 244)
DASHBOARD_SIZE_HORIZONTAL = (1093, 472)

# Отступы для различных тем и позиций
MARGIN_DEFAULT_TOP = "-40px 8px 8px 8px"
MARGIN_PILLS_TOP = "-40px 0px 0px 0px"
MARGIN_DENSE_EDGE_TOP = "-46px 0px 0px 0px"
MARGIN_PANEL = "0px 0px 0px 0px"
MARGIN_OPEN = "0px 8px 8px 8px"

# Размеры иконок
ICON_SIZE_WINDOW = 20
ICON_SIZE_VOLUME = 16

# Размеры вертикальных компонентов
VERT_COMP_SIZES = {
    "Pills": 38,
    "Dense": 50,
    "Edge": 44,
}


class AnchorConfig:
    """Конфигурация якоря и перехода для notch"""

    @staticmethod
    def get_anchor_and_transition(
        panel_theme: str,
        bar_position: str,
        panel_position: str,
        is_vertical: bool
    ) -> Tuple[str, str]:
        """Получить anchor и transition_type на основе настроек"""
        if panel_theme == "Notch":
            return "top", "slide-down"

        if panel_theme != "Panel":
            return "top", "slide-down"

        if is_vertical:
            return AnchorConfig._get_vertical_config(bar_position, panel_position)
        else:
            return AnchorConfig._get_horizontal_config(bar_position, panel_position)

    @staticmethod
    def _get_vertical_config(bar_position: str, panel_position: str) -> Tuple[str, str]:
        """Получить конфигурацию для вертикального режима"""
        configs = {
            "Left": {
                "Start": ("left top", "slide-right"),
                "Center": ("left", "slide-right"),
                "End": ("left bottom", "slide-right"),
            },
            "Right": {
                "Start": ("right top", "slide-left"),
                "Center": ("right", "slide-left"),
                "End": ("right bottom", "slide-left"),
            },
        }

        default = ("left", "slide-right") if bar_position == "Left" else ("right", "slide-left")
        return configs.get(bar_position, {}).get(panel_position, default)

    @staticmethod
    def _get_horizontal_config(bar_position: str, panel_position: str) -> Tuple[str, str]:
        """Получить конфигурацию для горизонтального режима"""
        configs = {
            "Top": {
                "Start": ("top left", "slide-down"),
                "Center": ("top", "slide-down"),
                "End": ("top right", "slide-down"),
            },
            "Bottom": {
                "Start": ("bottom left", "slide-up"),
                "Center": ("bottom", "slide-up"),
                "End": ("bottom right", "slide-up"),
            },
        }

        default = ("top", "slide-down") if bar_position == "Top" else ("bottom", "slide-up")
        return configs.get(bar_position, {}).get(panel_position, default)


class MarginCalculator:
    """Вычисление отступов для notch"""

    @staticmethod
    def calculate_margin(
        panel_theme: str,
        bar_theme: str,
        bar_position: str,
        is_vertical: bool
    ) -> str:
        """Вычислить отступы на основе настроек"""
        if panel_theme == "Panel":
            return MARGIN_PANEL

        if is_vertical:
            return MARGIN_PANEL

        if bar_position == "Bottom":
            return MARGIN_PANEL

        margin_map = {
            "Pills": MARGIN_PILLS_TOP,
            "Dense": MARGIN_DENSE_EDGE_TOP,
            "Edge": MARGIN_DENSE_EDGE_TOP,
        }

        return margin_map.get(bar_theme, MARGIN_DEFAULT_TOP)

class VolumeIconHelper:
    """Помощник для управления иконками и стилями громкости/микрофона"""

    @staticmethod
    def get_volume_icon_name(volume: int, is_muted: bool) -> str:
        """Получить имя иконки для громкости"""
        if is_muted or volume == 0:
            return "audio-volume-muted-symbolic"
        elif volume <= 33:
            return "audio-volume-low-symbolic"
        elif volume <= 66:
            return "audio-volume-medium-symbolic"
        else:
            return "audio-volume-high-symbolic"

    @staticmethod
    def get_microphone_icon_name(is_muted: bool) -> str:
        """Получить имя иконки для микрофона"""
        return "microphone-disabled-symbolic" if is_muted else "microphone-sensitivity-high-symbolic"

    @staticmethod
    def get_volume_style_class(volume: int, is_muted: bool) -> str:
        """Получить класс стиля для громкости"""
        if is_muted or volume == 0:
            return "volume-muted"
        elif volume <= 33:
            return "volume-low"
        elif volume <= 66:
            return "volume-medium"
        else:
            return "volume-high"

    @staticmethod
    def get_microphone_style_class(volume: int, is_muted: bool) -> str:
        """Получить класс стиля для микрофона"""
        if is_muted:
            return "mic-muted"
        elif volume <= 33:
            return "mic-low"
        elif volume <= 66:
            return "mic-medium"
        else:
            return "mic-high"

    @staticmethod
    def apply_style_classes(widget_styles: list, style_class: str):
        """Применить класс стиля к виджетам, удалив старые классы"""
        old_classes = ["volume-muted", "volume-low", "volume-medium", "volume-high",
                      "mic-muted", "mic-low", "mic-medium", "mic-high"]

        for style_context in widget_styles:
            for old_class in old_classes:
                style_context.remove_class(old_class)
            style_context.add_class(style_class)


class Notch(Window):
    """Главный виджет notch - выдвижная панель с виджетами"""

    def __init__(self, monitor_id: int = 0, **kwargs):
        self.monitor_id = monitor_id
        self.bar = kwargs.get("bar", None)

        # Инициализация состояния
        self._init_state_variables()

        # Получение монитор менеджера
        self._init_monitor_manager()

        # Вычисление параметров окна
        anchor_val, revealer_transition = self._calculate_window_params()
        margin_str = self._calculate_margin()

        # Инициализация базового окна
        super().__init__(
            name="notch",
            layer=3,
            anchor=anchor_val,
            margin=margin_str,
            keyboard_mode=0,
            exclusivity="none" if data.PANEL_THEME == "Notch" else "normal",
            visible=True,
            all_visible=True,
            monitor=monitor_id,
        )

        # Инициализация компонентов
        self._init_icon_resolver()
        self._init_modules()
        self._init_window_widgets()
        self._init_audio_widgets()
        self._init_compact_stack()
        self._init_main_stack()
        self._setup_layout(revealer_transition)
        self._setup_event_handlers()
        self._finalize_initialization()

    def _init_state_variables(self):
        """Инициализировать переменные состояния"""
        self._typed_chars_buffer = ""
        self._launcher_transitioning = False
        self._launcher_transition_timeout = None
        self._current_display_timeout_id = None
        self._suppress_first_audio_display = True
        self._is_notch_open = False
        self._scrolling = False
        self.is_hovered = False
        self._prevent_occlusion = False
        self._occlusion_timer_id = None
        self._forced_occlusion = False
        self._current_window_class = None
        self._last_window_title = None
        self._window_update_timeout_id = None

    def _init_monitor_manager(self):
        """Инициализировать монитор менеджер"""
        self.monitor_manager = None
        try:
            from utils.monitor_manager import get_monitor_manager
            self.monitor_manager = get_monitor_manager()
        except ImportError:
            pass

    def _calculate_window_params(self) -> Tuple[str, str]:
        """Вычислить параметры окна (anchor и transition)"""
        is_vertical = data.PANEL_THEME == "Panel" and data.VERTICAL
        return AnchorConfig.get_anchor_and_transition(
            data.PANEL_THEME,
            data.BAR_POSITION,
            data.PANEL_POSITION,
            is_vertical
        )

    def _calculate_margin(self) -> str:
        """Вычислить отступы окна"""
        is_vertical = data.PANEL_THEME == "Panel" and data.VERTICAL
        return MarginCalculator.calculate_margin(
            data.PANEL_THEME,
            data.BAR_THEME,
            data.BAR_POSITION,
            is_vertical
        )

    def _init_icon_resolver(self):
        """Инициализировать разрешение иконок"""
        self.icon_resolver = IconResolver()
        self._all_apps = get_desktop_applications()
        self.app_identifiers = self._build_app_identifiers_map()

    def _init_modules(self):
        """Инициализировать модули"""
        self.dashboard = Dashboard(notch=self)
        self.nhistory = self.dashboard.widgets.notification_history
        self.applet_stack = self.dashboard.widgets.applet_stack
        self.btdevices = self.dashboard.widgets.bluetooth
        self.nwconnections = self.dashboard.widgets.network_connections

        self.btdevices.set_visible(False)
        self.nwconnections.set_visible(False)

        self.launcher = AppLauncher(notch=self)
        self.overview = Overview(monitor_id=self.monitor_id)
        self.emoji = EmojiPicker(notch=self)
        self.power = PowerMenu(notch=self)
        self.tmux = TmuxManager(notch=self)
        self.cliphist = ClipHistory(notch=self)
        self.tools = Toolbox(notch=self)
        self.player_small = PlayerSmall()

    def _init_window_widgets(self):
        """Инициализировать виджеты окна"""
        self.window_label = Label(
            name="notch-window-label",
            h_expand=True,
            h_align="fill",
        )

        self.window_icon = Image(
            name="notch-window-icon",
            icon_name="application-x-executable",
            icon_size=ICON_SIZE_WINDOW
        )

        self.active_window = ActiveWindow(
            name="hyprland-window",
            h_expand=True,
            h_align="fill",
            formatter=FormattedString(
                "{'Desktop' if not win_title or win_title == 'unknown' else win_title}",
            ),
        )

        # Настройка виджета активного окна с debouncing
        self.active_window.connect("notify::label", self._on_window_label_changed)
        if data.PANEL_THEME == "Notch":
            self.active_window.connect("notify::label", self.on_active_window_changed)

        label_child = self.active_window.get_children()[0]
        label_child.set_hexpand(True)
        label_child.set_halign(Gtk.Align.FILL)
        label_child.set_ellipsize(Pango.EllipsizeMode.END)

        self.active_window.connect("notify::label", lambda *_: self.restore_label_properties())

        self.active_window_box = CenterBox(
            name="active-window-box",
            h_expand=True,
            h_align="fill",
            start_children=self.window_icon,
            center_children=self.active_window,
            end_children=None,
        )

        self.active_window_box.connect(
            "button-press-event",
            lambda w, e: (self.open_notch("dashboard"), False)[1],
        )

        self.user_label = Label(
            name="compact-user",
            label=f"{data.USERNAME}@{data.HOSTNAME}"
        )

    def _init_audio_widgets(self):
        """Инициализировать аудио виджеты"""
        self.audio = Audio()

        # Volume widgets
        self.volume_icon = Image(
            name="volume-display-icon",
            icon_name="audio-volume-high-symbolic",
            icon_size=ICON_SIZE_VOLUME
        )
        self.volume_icon.set_valign(Gtk.Align.CENTER)

        self.volume_label = Label(name="volume-display-label", label="...")
        self.volume_label.set_valign(Gtk.Align.CENTER)

        self.volume_bar = Gtk.ProgressBar(name="volume-display-bar")
        self.volume_bar.set_fraction(1.0)
        self.volume_bar.set_show_text(False)
        self.volume_bar.set_hexpand(False)
        self.volume_bar.set_valign(Gtk.Align.CENTER)

        self.volume_box = Box(
            name="volume-display-box",
            orientation="h",
            spacing=8,
            h_align="center",
            v_align="center",
            children=[self.volume_icon, self.volume_bar, self.volume_label]
        )

        # Microphone widgets
        self.mic_icon = Image(
            name="mic-display-icon",
            icon_name="microphone-sensitivity-high-symbolic",
            icon_size=ICON_SIZE_VOLUME
        )
        self.mic_icon.set_valign(Gtk.Align.CENTER)

        self.mic_label = Label(name="mic-display-label", label="...")
        self.mic_label.set_valign(Gtk.Align.CENTER)

        self.mic_bar = Gtk.ProgressBar(name="mic-display-bar")
        self.mic_bar.set_fraction(1.0)
        self.mic_bar.set_show_text(False)
        self.mic_bar.set_valign(Gtk.Align.CENTER)

        self.mic_box = Box(
            name="mic-display-box",
            orientation="h",
            spacing=8,
            h_align="center",
            v_align="center",
            children=[self.mic_icon, self.mic_bar, self.mic_label]
        )

    def _init_compact_stack(self):
        """Инициализировать компактный stack"""
        self.player_small.mpris_manager.connect(
            "player-appeared",
            lambda *_: self.compact_stack.set_visible_child(self.player_small),
        )
        self.player_small.mpris_manager.connect(
            "player-vanished",
            self.on_player_vanished
        )

        self.compact_stack = Stack(
            name="notch-compact-stack",
            v_expand=True,
            h_expand=True,
            transition_type="slide-up-down",
            transition_duration=100,
            children=[
                self.user_label,
                self.active_window_box,
                self.player_small,
                self.volume_box,
                self.mic_box,
            ],
        )
        self.compact_stack.set_visible_child(self.active_window_box)

        self.compact = Gtk.EventBox(name="notch-compact")
        self.compact.set_visible(True)
        self.compact.add(self.compact_stack)
        self.compact.add_events(
            Gdk.EventMask.SCROLL_MASK |
            Gdk.EventMask.BUTTON_PRESS_MASK |
            Gdk.EventMask.SMOOTH_SCROLL_MASK
        )

        self.compact.connect("scroll-event", self._on_compact_scroll)
        self.compact.connect(
            "button-press-event",
            lambda w, e: (self.open_notch("dashboard"), False)[1],
        )
        self.compact.connect("enter-notify-event", self.on_button_enter)
        self.compact.connect("leave-notify-event", self.on_button_leave)

    def _init_main_stack(self):
        """Инициализировать главный stack"""
        is_vertical = data.PANEL_THEME == "Panel" and data.VERTICAL

        style_classes = []
        if (not data.VERTICAL and data.BAR_THEME in ["Dense", "Edge"] and
                data.BAR_POSITION not in ["Bottom"]):
            style_classes.append("invert")

        self.stack = Stack(
            name="notch-content",
            v_expand=True,
            h_expand=True,
            style_classes=style_classes,
            transition_type="crossfade",
            transition_duration=250,
            children=[
                self.compact,
                self.launcher,
                self.dashboard,
                self.overview,
                self.emoji,
                self.power,
                self.tools,
                self.tmux,
                self.cliphist,
            ],
        )

        if data.PANEL_THEME == "Panel":
            self.stack.add_style_class("panel")
            self.stack.add_style_class(data.BAR_POSITION.lower())
            self.stack.add_style_class(data.PANEL_POSITION.lower())

        # Установка размеров
        self._set_widget_sizes(is_vertical)

        self.stack.set_interpolate_size(True)
        self.stack.set_homogeneous(False)

    def _set_widget_sizes(self, is_vertical: bool):
        """Установить размеры виджетов"""
        is_panel_vertical = is_vertical or (
            data.PANEL_POSITION in ["Start", "End"] and data.PANEL_THEME == "Panel"
        )

        if is_panel_vertical:
            self.compact.set_size_request(*COMPACT_SIZE_VERTICAL)
            self.launcher.set_size_request(*LAUNCHER_SIZE_VERTICAL)
            self.tmux.set_size_request(*LAUNCHER_SIZE_VERTICAL)
            self.cliphist.set_size_request(*LAUNCHER_SIZE_VERTICAL)
            self.dashboard.set_size_request(*DASHBOARD_SIZE_VERTICAL)
        else:
            self.compact.set_size_request(*COMPACT_SIZE_HORIZONTAL)
            self.launcher.set_size_request(*LAUNCHER_SIZE_HORIZONTAL)
            self.tmux.set_size_request(*LAUNCHER_SIZE_HORIZONTAL)
            self.cliphist.set_size_request(*LAUNCHER_SIZE_HORIZONTAL)
            self.dashboard.set_size_request(*DASHBOARD_SIZE_HORIZONTAL)

    def _setup_layout(self, revealer_transition: str):
        """Настроить layout виджетов"""
        self.corner_left = Box(
            name="notch-corner-left",
            orientation="v",
            h_align="start",
            children=[MyCorner("top-right")],
        )

        self.corner_right = Box(
            name="notch-corner-right",
            orientation="v",
            h_align="end",
            children=[MyCorner("top-left")],
        )

        self.notch_box = CenterBox(
            name="notch-box",
            orientation="h",
            h_align="center",
            v_align="center",
            start_children=self.corner_left,
            center_children=self.stack,
            end_children=self.corner_right,
        )
        self.notch_box.add_style_class(data.PANEL_THEME.lower())

        self.notch_revealer = Revealer(
            name="notch-revealer",
            transition_type=revealer_transition,
            transition_duration=250,
            child_revealed=True,
            child=self.notch_box,
        )
        self.notch_revealer.set_size_request(-1, 1)

        is_vertical = data.PANEL_THEME == "Panel" and data.VERTICAL
        self.notch_complete = Box(
            name="notch-complete",
            orientation="v" if is_vertical else "h",
            children=[self.notch_revealer],
        )

        # Вертикальные компоненты
        notch_children = [self.notch_complete]
        if data.VERTICAL:
            vert_size = VERT_COMP_SIZES.get(data.BAR_THEME, 38)
            if is_vertical:
                vert_size = 1

            self.vert_comp_left = Box(name="vert-comp")
            self.vert_comp_left.set_size_request(vert_size, 0)
            self.vert_comp_left.set_sensitive(False)

            self.vert_comp_right = Box(name="vert-comp")
            self.vert_comp_right.set_size_request(vert_size, 0)
            self.vert_comp_right.set_sensitive(False)

            notch_children = [
                self.vert_comp_left,
                self.notch_complete,
                self.vert_comp_right,
            ]

        self.notch_wrap = Box(name="notch-wrap", children=notch_children)

        # Создание hover eventbox для Notch темы
        if data.PANEL_THEME == "Notch":
            self.hover_eventbox = Gtk.EventBox(name="notch-hover-eventbox")
            self.hover_eventbox.add(self.notch_wrap)
            self.hover_eventbox.set_visible(True)
            self.hover_eventbox.set_size_request(260, 4)
            self.hover_eventbox.add_events(
                Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK
            )
            self.hover_eventbox.connect("enter-notify-event", self.on_notch_hover_area_enter)
            self.hover_eventbox.connect("leave-notify-event", self.on_notch_hover_area_leave)
            self.add(self.hover_eventbox)
        else:
            self.add(self.notch_wrap)

    def _setup_event_handlers(self):
        """Настроить обработчики событий"""
        self.connect("realize", self._on_realize)
        self.connect("key-press-event", self.on_key_press)

        self.add_keybinding("Escape", lambda *_: self.close_notch())
        self.add_keybinding("Ctrl Tab", lambda *_: self.dashboard.go_to_next_child())
        self.add_keybinding(
            "Ctrl Shift ISO_Left_Tab",
            lambda *_: self.dashboard.go_to_previous_child()
        )

        self.active_window.connect(
            "button-press-event",
            lambda w, e: (self.open_notch("dashboard"), False)[1],
        )

    def _finalize_initialization(self):
        """Завершить инициализацию"""
        self.show_all()

        # Скрыть углы для Panel темы
        if data.PANEL_THEME != "Notch":
            for corner in [self.corner_left, self.corner_right]:
                corner.set_visible(False)

        # Установить видимость revealer
        if data.PANEL_THEME == "Notch":
            self.notch_revealer.set_reveal_child(True)
        else:
            self.notch_revealer.set_reveal_child(False)

        # Запустить инициализацию аудио
        GLib.timeout_add(100, self._connect_audio_signals)

        # Обновить иконку окна
        self._debounced_update_window_icon()

        # Запустить проверку окклюзии
        self._current_window_class = self._get_current_window_class()
        GLib.timeout_add(OCCLUSION_CHECK_INTERVAL, self._check_occlusion)

    # ==================== Аудио методы ====================

    def _connect_audio_signals(self, retry_count: int = 0) -> bool:
        """Подключить сигналы аудио с повторными попытками"""
        try:
            if self.audio:
                self.audio.connect("notify::speaker", self._on_speaker_changed)
                self.audio.connect("notify::microphone", self._on_microphone_changed)

                if self.audio.speaker:
                    self.audio.speaker.connect("changed", self._on_speaker_changed_signal)
                    GLib.idle_add(self._update_volume_widgets_silently)

                if self.audio.microphone:
                    self.audio.microphone.connect("changed", self._on_microphone_changed_signal)
                    GLib.idle_add(self._update_mic_widgets_silently)

                GLib.timeout_add(AUDIO_DISPLAY_ENABLE_DELAY, self._enable_audio_display)
                return False
        except Exception as e:
            print(f"Audio connection error (attempt {retry_count + 1}): {e}")
            if retry_count < MAX_AUDIO_CONNECTION_RETRIES - 1:
                GLib.timeout_add(
                    AUDIO_CONNECTION_RETRY_INTERVAL,
                    lambda: self._connect_audio_signals(retry_count + 1)
                )
        return False

    def _on_speaker_changed(self, audio_service, speaker):
        """Обработать изменение динамика"""
        if self.audio.speaker:
            try:
                self.audio.speaker.disconnect_by_func(self._on_speaker_changed_signal)
            except:
                pass
            self.audio.speaker.connect("changed", self._on_speaker_changed_signal)
            self._update_volume_widgets_silently()

    def _on_microphone_changed(self, audio_service, microphone):
        """Обработать изменение микрофона"""
        if self.audio.microphone:
            try:
                self.audio.microphone.disconnect_by_func(self._on_microphone_changed_signal)
            except:
                pass
            self.audio.microphone.connect("changed", self._on_microphone_changed_signal)
            self._update_mic_widgets_silently()

    def _on_speaker_changed_signal(self, speaker, *args):
        """Обработать сигнал изменения динамика"""
        self._handle_speaker_change()

    def _on_microphone_changed_signal(self, microphone, *args):
        """Обработать сигнал изменения микрофона"""
        self._handle_microphone_change()

    def _handle_speaker_change(self):
        """Обработать изменение громкости"""
        if not self.audio or not self.audio.speaker:
            return

        if self._suppress_first_audio_display:
            self._update_volume_widgets_silently()
            return

        speaker = self.audio.speaker
        volume_int = int(round(speaker.volume))
        is_muted = speaker.muted

        self._update_volume_display(volume_int, is_muted)

        if not self._is_notch_open:
            self.show_volume_display()

    def _handle_microphone_change(self):
        """Обработать изменение микрофона"""
        if not self.audio or not self.audio.microphone:
            return

        if self._suppress_first_audio_display:
            self._update_mic_widgets_silently()
            return

        microphone = self.audio.microphone
        volume_int = int(round(microphone.volume))
        is_muted = microphone.muted

        self._update_mic_display(volume_int, is_muted)

        if not self._is_notch_open:
            self.show_mic_display()

    def _update_volume_display(self, volume: int, is_muted: bool):
        """Обновить отображение громкости"""
        self.volume_bar.set_fraction(volume / 100.0)

        icon_name = VolumeIconHelper.get_volume_icon_name(volume, is_muted)
        self.volume_icon.set_from_icon_name(icon_name, ICON_SIZE_VOLUME)

        label_text = "Muted" if (is_muted or volume == 0) else f"{volume}%"
        self.volume_label.set_text(label_text)

        style_class = VolumeIconHelper.get_volume_style_class(volume, is_muted)
        widget_styles = [
            self.volume_box.get_style_context(),
            self.volume_icon.get_style_context(),
            self.volume_bar.get_style_context()
        ]
        VolumeIconHelper.apply_style_classes(widget_styles, style_class)

    def _update_mic_display(self, volume: int, is_muted: bool):
        """Обновить отображение микрофона"""
        self.mic_bar.set_fraction(volume / 100.0)

        icon_name = VolumeIconHelper.get_microphone_icon_name(is_muted)
        self.mic_icon.set_from_icon_name(icon_name, ICON_SIZE_VOLUME)

        label_text = "Muted" if is_muted else f"{volume}%"
        self.mic_label.set_text(label_text)

        style_class = VolumeIconHelper.get_microphone_style_class(volume, is_muted)
        widget_styles = [
            self.mic_box.get_style_context(),
            self.mic_icon.get_style_context(),
            self.mic_bar.get_style_context()
        ]
        VolumeIconHelper.apply_style_classes(widget_styles, style_class)

    def _update_volume_widgets_silently(self):
        """Обновить виджеты громкости без показа"""
        if not self.audio or not self.audio.speaker:
            return

        speaker = self.audio.speaker
        volume_int = int(round(speaker.volume))
        self._update_volume_display(volume_int, speaker.muted)

    def _update_mic_widgets_silently(self):
        """Обновить виджеты микрофона без показа"""
        if not self.audio or not self.audio.microphone:
            return

        microphone = self.audio.microphone
        volume_int = int(round(microphone.volume))
        self._update_mic_display(volume_int, microphone.muted)

    def _enable_audio_display(self) -> bool:
        """Включить отображение изменений аудио"""
        self._suppress_first_audio_display = False
        return False

    def show_volume_display(self):
        """Показать дисплей громкости"""
        if self._is_notch_open:
            return

        if self._current_display_timeout_id:
            GLib.source_remove(self._current_display_timeout_id)

        self.compact_stack.set_visible_child(self.volume_box)
        self._current_display_timeout_id = GLib.timeout_add(
            VOLUME_DISPLAY_DURATION,
            self.return_to_normal_view
        )

    def show_mic_display(self):
        """Показать дисплей микрофона"""
        if self._is_notch_open:
            return

        if self._current_display_timeout_id:
            GLib.source_remove(self._current_display_timeout_id)

        self.compact_stack.set_visible_child(self.mic_box)
        self._current_display_timeout_id = GLib.timeout_add(
            VOLUME_DISPLAY_DURATION,
            self.return_to_normal_view
        )

    def return_to_normal_view(self) -> bool:
        """Вернуться к нормальному виду"""
        self._current_display_timeout_id = None
        if not self._is_notch_open:
            current_child = self.compact_stack.get_visible_child()
            if current_child in [self.volume_box, self.mic_box]:
                self.compact_stack.set_visible_child(self.active_window_box)
        return False

    # ==================== Методы UI ====================

    def on_button_enter(self, widget, event) -> bool:
        """Обработать вход курсора на кнопку"""
        self.is_hovered = True
        window = widget.get_window()
        if window:
            window.set_cursor(Gdk.Cursor(Gdk.CursorType.HAND2))
        return True

    def on_button_leave(self, widget, event) -> bool:
        """Обработать выход курсора с кнопки"""
        if event.detail == Gdk.NotifyType.INFERIOR:
            return False

        self.is_hovered = False
        window = widget.get_window()
        if window:
            window.set_cursor(None)
        return True

    def _on_realize(self, widget):
        """Обработать реализацию окна"""
        self.get_window().raise_()

    def on_notch_hover_area_enter(self, widget, event) -> bool:
        """Обработать вход в область наведения notch"""
        self.is_hovered = True
        if data.PANEL_THEME == "Notch" and data.BAR_POSITION != "Top":
            self.notch_revealer.set_reveal_child(True)
        return False

    def on_notch_hover_area_leave(self, widget, event) -> bool:
        """Обработать выход из области наведения notch"""
        if event.detail == Gdk.NotifyType.INFERIOR:
            return False
        self.is_hovered = False
        return False

    def on_player_vanished(self, *args):
        """Обработать исчезновение плеера"""
        if self.player_small.mpris_label.get_label() == "Nothing Playing":
            self.compact_stack.set_visible_child(self.active_window_box)

    def restore_label_properties(self):
        """Восстановить свойства label"""
        label = self.active_window.get_children()[0]
        if isinstance(label, Gtk.Label):
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_hexpand(True)
            label.set_halign(Gtk.Align.FILL)
            label.queue_resize()
        self.update_window_icon()

    def _on_compact_scroll(self, widget, event) -> bool:
        """Обработать прокрутку на компактном виде"""
        if self._scrolling:
            return True

        children = self.compact_stack.get_children()
        current = children.index(self.compact_stack.get_visible_child())
        new_index = current

        if event.direction == Gdk.ScrollDirection.SMOOTH:
            if event.delta_y < -0.1:
                new_index = (current - 1) % len(children)
            elif event.delta_y > 0.1:
                new_index = (current + 1) % len(children)
            else:
                return False
        elif event.direction == Gdk.ScrollDirection.UP:
            new_index = (current - 1) % len(children)
        elif event.direction == Gdk.ScrollDirection.DOWN:
            new_index = (current + 1) % len(children)
        else:
            return False

        self.compact_stack.set_visible_child(children[new_index])
        self._scrolling = True
        GLib.timeout_add(SCROLL_DEBOUNCE_DELAY, self._reset_scrolling)
        return True

    def _reset_scrolling(self) -> bool:
        """Сбросить флаг прокрутки"""
        self._scrolling = False
        return False

    def toggle_hidden(self):
        """Переключить скрытие"""
        self.hidden = not self.hidden
        self.set_visible(not self.hidden)

  # ==================== Управление Notch (ИСПРАВЛЕНО) ====================

    def close_notch(self):
        """Закрыть notch"""
        if self.monitor_manager:
            self.monitor_manager.set_notch_state(self.monitor_id, False)

        self.set_keyboard_mode(0)
        self.notch_box.remove_style_class("open")
        self.stack.remove_style_class("open")

        if self.bar:
            self.bar.revealer_right.set_reveal_child(True)
            self.bar.revealer_left.set_reveal_child(True)

        self.applet_stack.set_visible_child(self.nhistory)
        self._is_notch_open = False
        self.stack.set_visible_child(self.compact)

        if data.PANEL_THEME != "Notch":
            self.notch_revealer.set_reveal_child(False)

        # Восстановить отступы для скрытого бара
        if self.bar and not self.bar.get_visible() and data.BAR_POSITION == "Top":
            if data.BAR_THEME == "Pills":
                self.set_margin(MARGIN_PILLS_TOP)
            elif data.BAR_THEME in ["Dense", "Edge"]:
                self.set_margin(MARGIN_DENSE_EDGE_TOP)
            else:
                self.set_margin(MARGIN_DEFAULT_TOP)

    def open_notch(self, widget_name: str):
        """Открыть notch с указанным виджетом"""
        # Обработка мультимониторной фокусировки
        if self.monitor_manager:
            real_focused_monitor_id = self._get_real_focused_monitor_id()

            if real_focused_monitor_id is not None:
                self.monitor_manager._focused_monitor_id = real_focused_monitor_id

            focused_monitor_id = self.monitor_manager.get_focused_monitor_id()
            if focused_monitor_id != self.monitor_id:
                self.close_notch()
                focused_notch = self.monitor_manager.get_instance(focused_monitor_id, 'notch')
                if focused_notch and hasattr(focused_notch, 'open_notch'):
                    focused_notch._open_notch_internal(widget_name)
                return

            self.monitor_manager.close_all_notches_except(self.monitor_id)
            self.monitor_manager.set_notch_state(self.monitor_id, True, widget_name)

        self._open_notch_internal(widget_name)

    def _get_real_focused_monitor_id(self) -> Optional[int]:
        """Получить ID реального сфокусированного монитора"""
        self._focused_monitor_result = None
        GLib.Thread.new("get-focused-monitor", self._get_focused_monitor_thread, None)

        import time
        start = time.time()
        while self._focused_monitor_result is None and time.time() - start < 2.0:
            time.sleep(0.01)

        return self._focused_monitor_result

    def _get_focused_monitor_thread(self, user_data):
        """Поток для получения сфокусированного монитора"""
        try:
            result = subprocess.run(
                ["hyprctl", "monitors", "-j"],
                capture_output=True,
                text=True,
                check=True,
                timeout=2.0
            )
            monitors = json.loads(result.stdout)
            for i, monitor in enumerate(monitors):
                if monitor.get('focused', False):
                    self._focused_monitor_result = i
                    return
        except (subprocess.CalledProcessError, json.JSONDecodeError,
                FileNotFoundError, subprocess.TimeoutExpired) as e:
            logger.warning(f"Could not get focused monitor from Hyprland: {e}")
            self._focused_monitor_result = None

    def _open_notch_internal(self, widget_name: str):
        """Внутренняя реализация открытия notch"""
        self.notch_revealer.set_reveal_child(True)
        self.notch_box.add_style_class("open")
        self.stack.add_style_class("open")

        current_stack_child = self.stack.get_visible_child()
        is_dashboard_visible = current_stack_child == self.dashboard

        # Обработка специальных виджетов
        if self._handle_special_widget(widget_name, is_dashboard_visible):
            return

        # Обработка dashboard секций (ИСПРАВЛЕНО)
        if self._handle_dashboard_section(widget_name, is_dashboard_visible):
            return

        # Обработка других виджетов
        widget_config = self._get_widget_config(widget_name)
        if widget_config:
            self._open_widget_from_config(widget_config, widget_name)
        else:
            self._open_dashboard_default(widget_name)

    def _handle_special_widget(self, widget_name: str, is_dashboard_visible: bool) -> bool:
        """Обработать специальные виджеты (network, bluetooth, dashboard)"""
        special_widgets = {
            "network_applet": (self.nwconnections, "widgets"),
            "bluetooth": (self.btdevices, "widgets"),
            "dashboard": (self.nhistory, "widgets"),
        }

        if widget_name not in special_widgets:
            return False

        target_widget, section = special_widgets[widget_name]

        if is_dashboard_visible:
            if (self.dashboard.stack.get_visible_child() == self.dashboard.widgets and
                    self.applet_stack.get_visible_child() == target_widget):
                self.close_notch()
                return True

        self.set_keyboard_mode(1)
        self.dashboard.go_to_section(section)
        self.applet_stack.set_visible_child(target_widget)

        # ИСПРАВЛЕНО: добавлено открытие dashboard
        self.stack.set_visible_child(self.dashboard)
        self._update_bar_revealers(True)

        if self.bar and not self.bar.get_visible() and data.BAR_POSITION == "Top":
            self.set_margin(MARGIN_OPEN)

        self._is_notch_open = True
        return True

    def _handle_dashboard_section(self, widget_name: str, is_dashboard_visible: bool) -> bool:
        """Обработать секции dashboard (ИСПРАВЛЕНО)"""
        dashboard_sections = {
            "pins": self.dashboard.pins,
            "kanban": self.dashboard.kanban,
            "wallpapers": self.dashboard.wallpapers,
            "mixer": self.dashboard.mixer,
        }

        if widget_name not in dashboard_sections:
            return False

        section_widget = dashboard_sections[widget_name]

        # Если уже открыт этот раздел, закрыть
        if is_dashboard_visible and self.dashboard.stack.get_visible_child() == section_widget:
            self.close_notch()
            return True

        # ИСПРАВЛЕНО: Открыть этот раздел dashboard
        self.set_keyboard_mode(1)
        self.stack.set_visible_child(self.dashboard)
        self.dashboard.go_to_section(widget_name)
        self._update_bar_revealers(True)

        if self.bar and not self.bar.get_visible() and data.BAR_POSITION == "Top":
            self.set_margin(MARGIN_OPEN)

        self._is_notch_open = True
        return True

    def _get_widget_config(self, widget_name: str) -> Optional[Dict[str, Any]]:
        """Получить конфигурацию виджета"""
        configs = {
            "tmux": {
                "instance": self.tmux,
                "action": self.tmux.open_manager
            },
            "cliphist": {
                "instance": self.cliphist,
                "action": lambda: GLib.idle_add(self.cliphist.open),
            },
            "launcher": {
                "instance": self.launcher,
                "action": self.launcher.open_launcher,
                "focus": lambda: (
                    self.launcher.search_entry.set_text(""),
                    self.launcher.search_entry.grab_focus(),
                ),
            },
            "emoji": {
                "instance": self.emoji,
                "action": self.emoji.open_picker,
                "focus": lambda: (
                    self.emoji.search_entry.set_text(""),
                    self.emoji.search_entry.grab_focus(),
                ),
            },
            "overview": {
                "instance": self.overview,
                "hide_revealers": True
            },
            "power": {"instance": self.power},
            "tools": {"instance": self.tools},
        }

        return configs.get(widget_name)

    def _open_widget_from_config(self, config: Dict[str, Any], widget_name: str):
        """Открыть виджет из конфигурации"""
        target_widget = config["instance"]
        current_child = self.stack.get_visible_child()

        if current_child == target_widget:
            self.close_notch()
            return

        self.set_keyboard_mode(1)
        self.stack.set_visible_child(target_widget)

        if "action" in config:
            config["action"]()

        if "focus" in config:
            config["focus"]()

        hide_revealers = config.get("hide_revealers", False)
        self._update_bar_revealers(hide_revealers)

        if self.bar and not self.bar.get_visible() and data.BAR_POSITION == "Top":
            self.set_margin(MARGIN_OPEN)

        self._is_notch_open = True

    def _open_dashboard_default(self, widget_name: str):
        """Открыть dashboard по умолчанию"""
        self.set_keyboard_mode(1)
        self.stack.set_visible_child(self.dashboard)

        # По умолчанию widgets + nhistory
        self.dashboard.go_to_section("widgets")
        self.applet_stack.set_visible_child(self.nhistory)

        self._update_bar_revealers(True)

        if self.bar and not self.bar.get_visible() and data.BAR_POSITION == "Top":
            self.set_margin(MARGIN_OPEN)

        self._is_notch_open = True

    def _update_bar_revealers(self, hide: bool):
        """Обновить видимость bar revealers"""
        if not self.bar:
            return

        show_revealers = not hide

        # Для Bottom позиции или Panel темы всегда показывать
        if ((data.BAR_POSITION in ["Top", "Bottom"] and data.PANEL_THEME == "Panel") or
                (data.BAR_POSITION in ["Bottom"] and data.PANEL_THEME == "Notch")):
            show_revealers = True

        self.bar.revealer_right.set_reveal_child(show_revealers)
        self.bar.revealer_left.set_reveal_child(show_revealers)

    # ==================== Иконки и приложения ====================

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
                exe_basename = app.executable.split("/")[-1].lower()
                identifiers[exe_basename] = app
            if app.command_line:
                cmd_base = app.command_line.split()[0].split("/")[-1].lower()
                identifiers[cmd_base] = app
        return identifiers

    def find_app(self, app_id: str) -> Optional[Any]:
        """Найти приложение по идентификатору"""
        normalized_id = app_id.lower()
        return self.app_identifiers.get(normalized_id)

    def update_window_icon(self, *args):
        """Обновить иконку окна"""
        label_widget = self.active_window.get_children()[0]
        if not isinstance(label_widget, Gtk.Label):
            return

        title = label_widget.get_text()
        if title == "Desktop" or not title:
            self.window_icon.set_visible(False)
            return

        self.window_icon.set_visible(True)

        # Получить app_id из Hyprland
        app_id = self._get_active_window_app_id()
        if not app_id:
            self._set_fallback_icon()
            return

        # Попытаться получить иконку
        icon_pixbuf = self._get_app_icon_pixbuf(app_id)

        if icon_pixbuf:
            self.window_icon.set_from_pixbuf(icon_pixbuf)
        else:
            self._set_fallback_icon()

    def _get_active_window_app_id(self) -> Optional[str]:
        """Получить app_id активного окна"""
        try:
            from fabric.hyprland.widgets import get_hyprland_connection
            conn = get_hyprland_connection()
            if not conn:
                return None

            active_window_json = conn.send_command("j/activewindow").reply.decode()
            active_window_data = json.loads(active_window_json)
            return (active_window_data.get("initialClass", "") or 
                   active_window_data.get("class", ""))
        except Exception as e:
            logger.error(f"Error getting active window app_id: {e}")
            return None

    def _get_app_icon_pixbuf(self, app_id: str):
        """Получить pixbuf иконки приложения"""
        # Попытка через desktop app
        desktop_app = self.find_app(app_id)
        if desktop_app:
            icon_pixbuf = desktop_app.get_icon_pixbuf(size=ICON_SIZE_WINDOW)
            if icon_pixbuf:
                return icon_pixbuf

        # Попытка через icon resolver
        icon_pixbuf = self.icon_resolver.get_icon_pixbuf(app_id, ICON_SIZE_WINDOW)
        if icon_pixbuf:
            return icon_pixbuf

        # Попытка с базовым именем (если есть дефис)
        if "-" in app_id:
            base_app_id = app_id.split("-")[0]
            return self.icon_resolver.get_icon_pixbuf(base_app_id, ICON_SIZE_WINDOW)

        return None

    def _set_fallback_icon(self):
        """Установить запасную иконку"""
        try:
            self.window_icon.set_from_icon_name("application-x-executable", ICON_SIZE_WINDOW)
        except:
            self.window_icon.set_from_icon_name("application-x-executable-symbolic", ICON_SIZE_WINDOW)

    def _on_window_label_changed(self, *args):
        """Обработать изменение заголовка окна с debouncing"""
        current_title = self.active_window.get_label()
        if current_title == self._last_window_title:
            return
        self._last_window_title = current_title

        # Cancel any pending update
        if self._window_update_timeout_id:
            GLib.source_remove(self._window_update_timeout_id)

        # Schedule debounced update
        self._window_update_timeout_id = GLib.timeout_add(100, self._debounced_update_window_icon)

    def _debounced_update_window_icon(self) -> bool:
        """Обновить иконку окна с debouncing"""
        self._window_update_timeout_id = None
        self.update_window_icon()
        return False

    def _get_current_window_class(self) -> str:
        """Получить класс текущего активного окна"""
        try:
            from fabric.hyprland.widgets import get_hyprland_connection
            conn = get_hyprland_connection()
            if conn:
                active_window_json = conn.send_command("j/activewindow").reply.decode()
                active_window_data = json.loads(active_window_json)
                return (active_window_data.get("initialClass", "") or 
                       active_window_data.get("class", ""))
        except Exception as e:
            logger.error(f"Error getting window class: {e}")
        return ""

    # ==================== Окклюзия ====================

    def _check_occlusion(self) -> bool:
        """Проверить окклюзию notch"""
        if self._forced_occlusion:
            self.notch_revealer.set_reveal_child(self.is_hovered)
        elif not (self.is_hovered or self._is_notch_open or self._prevent_occlusion):
            is_occluded = check_occlusion(("top", 40))
            self.notch_revealer.set_reveal_child(not is_occluded)
        return True

    def force_occlusion(self):
        """Принудительно скрыть notch"""
        self._forced_occlusion = True
        self._prevent_occlusion = False
        self.notch_revealer.set_reveal_child(False)

        if data.BAR_POSITION in ["Left", "Right"]:
            GLib.timeout_add(100, self._check_occlusion)

    def restore_from_occlusion(self):
        """Восстановить notch из режима окклюзии"""
        self._forced_occlusion = False
        if data.PANEL_THEME == "Notch":
            if data.BAR_POSITION == "Top":
                self.notch_revealer.set_reveal_child(True)
            else:
                self._prevent_occlusion = False

    def on_active_window_changed(self, *args):
        """Обработать изменение активного окна (для Notch темы)"""
        if data.PANEL_THEME != "Notch":
            return

        new_window_class = self._get_current_window_class()
        if new_window_class == self._current_window_class:
            return

        self._current_window_class = new_window_class

        # Сбросить существующий таймер
        if self._occlusion_timer_id is not None:
            GLib.source_remove(self._occlusion_timer_id)
            self._occlusion_timer_id = None

        # Временно запретить окклюзию
        self._prevent_occlusion = True
        if not self.notch_revealer.get_reveal_child():
            self.notch_revealer.set_reveal_child(True)

        # Восстановить окклюзию через задержку
        self._occlusion_timer_id = GLib.timeout_add(
            OCCLUSION_RESTORE_DELAY,
            self._restore_occlusion_check,
        )

    def _restore_occlusion_check(self) -> bool:
        """Восстановить проверку окклюзии"""
        self._prevent_occlusion = False
        self._occlusion_timer_id = None
        return False

    # ==================== Launcher ====================

    def open_launcher_with_text(self, initial_text: str):
        """Открыть launcher с начальным текстом"""
        self._launcher_transitioning = True

        if initial_text:
            self._typed_chars_buffer = initial_text

        if self.stack.get_visible_child() == self.launcher:
            current_text = self.launcher.search_entry.get_text()
            self.launcher.search_entry.set_text(current_text + initial_text)
            self.launcher.search_entry.set_position(-1)
            self.launcher.search_entry.select_region(-1, -1)
            self.launcher.search_entry.grab_focus()
            return

        self.set_keyboard_mode(1)

        # Очистить стили
        for style in ["launcher", "dashboard", "notification", "overview", 
                     "emoji", "power", "tools", "tmux"]:
            self.stack.remove_style_class(style)

        for w in [self.launcher, self.dashboard, self.overview, self.emoji,
                 self.power, self.tools, self.tmux, self.cliphist]:
            w.remove_style_class("open")

        self.stack.add_style_class("launcher")
        self.stack.set_visible_child(self.launcher)
        self.launcher.add_style_class("open")
        self.launcher.ensure_initialized()
        self.launcher.open_launcher()

        if self._launcher_transition_timeout:
            GLib.source_remove(self._launcher_transition_timeout)

        self._launcher_transition_timeout = GLib.timeout_add(
            LAUNCHER_TRANSITION_DELAY,
            self._finalize_launcher_transition
        )

        if self.bar:
            self.bar.revealer_right.set_reveal_child(True)
            self.bar.revealer_left.set_reveal_child(True)

        self._is_notch_open = True

    def _finalize_launcher_transition(self) -> bool:
        """Завершить переход launcher"""
        if self._typed_chars_buffer:
            entry = self.launcher.search_entry
            entry.set_text(self._typed_chars_buffer)
            entry.grab_focus()

            # Убрать выделение текста
            for delay in [10, 50, 100]:
                GLib.timeout_add(delay, self._ensure_no_text_selection)

            logger.info(f"Applied buffered text: '{self._typed_chars_buffer}'")
            self._typed_chars_buffer = ""

        self._launcher_transitioning = False
        self._launcher_transition_timeout = None
        return False

    def _ensure_no_text_selection(self) -> bool:
        """Убедиться что нет выделения текста"""
        entry = self.launcher.search_entry
        text_len = len(entry.get_text())
        entry.set_position(text_len)
        entry.select_region(text_len, text_len)

        if not entry.has_focus():
            entry.grab_focus()
            GLib.idle_add(lambda: entry.select_region(text_len, text_len))

        return False

    def on_key_press(self, widget, event) -> bool:
        """Обработать нажатие клавиши"""
        keyval = event.keyval

        # Во время перехода launcher буферизировать символы
        if self._launcher_transitioning:
            if self._is_valid_char(keyval):
                keychar = chr(keyval)
                self._typed_chars_buffer += keychar
                logger.info(f"Buffered: {keychar}, buffer: '{self._typed_chars_buffer}'")
                return True

        # Если dashboard открыт и не launcher, открыть launcher при вводе
        if (self.stack.get_visible_child() == self.dashboard and
                self.dashboard.stack.get_visible_child() == self.dashboard.widgets):
            if self.stack.get_visible_child() != self.launcher:
                if self._is_valid_char(keyval):
                    keychar = chr(keyval)
                    logger.info(f"Notch received keypress: {keychar}")
                    self.open_launcher_with_text(keychar)
                    return True

        return False

    def _is_valid_char(self, keyval: int) -> bool:
        """Проверить является ли символ валидным для ввода"""
        return (
            (keyval >= Gdk.KEY_a and keyval <= Gdk.KEY_z) or
            (keyval >= Gdk.KEY_A and keyval <= Gdk.KEY_Z) or
            (keyval >= Gdk.KEY_0 and keyval <= Gdk.KEY_9) or
            keyval in (Gdk.KEY_space, Gdk.KEY_underscore, Gdk.KEY_minus, Gdk.KEY_period)
        )
