import gi

gi.require_version("Gtk", "3.0")

from fabric.widgets.box import Box
from fabric.widgets.stack import Stack

import config.data as data

from modules.bluetooth import BluetoothConnections
from modules.buttons import Buttons
from modules.calendar import Calendar
from modules.controls import ControlSliders
from modules.metrics import Metrics
from modules.network import NetworkConnections
from modules.notifications import NotificationHistory
from modules.player import Player


class Widgets(Box):
    """
    Центральный контейнер дашборда:
      - слева/сверху: календарь + стек апплетов (уведомления/сеть/Bluetooth)
      - справа/снизу: кнопки, слайдеры, плеер, метрики.
    """

    def __init__(self, notch, **kwargs):
        # Вычисляем ориентацию один раз
        vertical_layout = (
            data.PANEL_THEME == "Panel"
            and (
                data.BAR_POSITION in ["Left", "Right"]
                or data.PANEL_POSITION in ["Start", "End"]
            )
        )

        super().__init__(
            name="dash-widgets",
            h_align="fill",
            v_align="fill",
            h_expand=True,
            v_expand=True,
            visible=True,
            all_visible=True,
            **kwargs,
        )

        self.notch = notch

        # Календарь: неделя для вертикального, месяц для горизонтального
        calendar_view_mode = "week" if vertical_layout else "month"
        self.calendar = Calendar(view_mode=calendar_view_mode)

        # Крупные виджеты
        self.buttons = Buttons(widgets=self)
        self.bluetooth = BluetoothConnections(widgets=self)
        self.controls = ControlSliders()
        self.player = Player()
        self.metrics = Metrics()
        self.notification_history = NotificationHistory()
        self.network_connections = NetworkConnections(widgets=self)

        # Стек апплетов (уведомления / сеть / Bluetooth)
        self.applet_stack = Stack(
            h_expand=True,
            v_expand=True,
            transition_type="slide-left-right",
            children=[
                self.notification_history,
                self.network_connections,
                self.bluetooth,
            ],
        )

        self.applet_stack_box = Box(
            name="applet-stack",
            h_expand=True,
            v_expand=True,
            h_align="fill",
            children=[self.applet_stack],
        )

        # Левая/верхняя часть (container_1)
        if not vertical_layout:
            # Горизонтальная раскладка: календарь + стек справа, метрики снизу
            children_1 = [
                Box(
                    name="container-sub-1",
                    h_expand=True,
                    v_expand=True,
                    spacing=8,
                    children=[
                        self.calendar,
                        self.applet_stack_box,
                    ],
                ),
                self.metrics,
            ]
        else:
            # Вертикальная раскладка: стек → календарь → плеер
            children_1 = [
                self.applet_stack_box,
                self.calendar,
                self.player,
            ]

        self.container_1 = Box(
            name="container-1",
            h_expand=True,
            v_expand=True,
            orientation="h" if not vertical_layout else "v",
            spacing=8,
            children=children_1,
        )

        # Правая/нижняя часть (кнопки + слайдеры + container_1)
        self.container_2 = Box(
            name="container-2",
            h_expand=True,
            v_expand=True,
            orientation="v",
            spacing=8,
            children=[
                self.buttons,
                self.controls,
                self.container_1,
            ],
        )

        # Самый внешний контейнер (container_3)
        if not vertical_layout:
            children_3 = [
                self.player,
                self.container_2,
            ]
        else:
            children_3 = [
                self.container_2,
            ]

        self.container_3 = Box(
            name="container-3",
            h_expand=True,
            v_expand=True,
            orientation="h",
            spacing=8,
            children=children_3,
        )

        self.add(self.container_3)

    # --- публичные методы переключения стека апплетов ---

    def show_bt(self):
        """Показать вкладку Bluetooth в стеке апплетов."""
        if self.bluetooth in self.applet_stack.get_children():
            self.applet_stack.set_visible_child(self.bluetooth)

    def show_notif(self):
        """Показать историю уведомлений в стеке апплетов."""
        if self.notification_history in self.applet_stack.get_children():
            self.applet_stack.set_visible_child(self.notification_history)

    def show_network_applet(self):
        """Открыть сетевой апплет через notch (как и раньше)."""
        if self.notch is not None:
            self.notch.open_notch("network_applet")
