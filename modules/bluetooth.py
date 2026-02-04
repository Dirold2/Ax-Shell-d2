from fabric.bluetooth import BluetoothClient, BluetoothDevice
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.image import Image
from fabric.widgets.label import Label
from fabric.widgets.scrolledwindow import ScrolledWindow
from gi.repository import Gdk, GLib
import modules.icons as icons
from .buttons import add_hover_cursor


class BluetoothDeviceSlot(CenterBox):
    """
    Виджет для отображения одного Bluetooth устройства

    Улучшения:
    - Кеширование состояний для избежания лишних обновлений UI
    - Debouncing для пересортировки
    - Улучшенная обработка ошибок
    - Автоматическая очистка ресурсов
    """

    def __init__(self, device: BluetoothDevice, owner, **kwargs):
        super().__init__(name="bluetooth-device", **kwargs)

        self.device = device
        self.owner = owner

        # Кеш состояний для избежания лишних обновлений UI
        self._cached_connected = None
        self._cached_connecting = None
        self._cached_label = None

        # Debouncing для пересортировки
        self._resort_timeout_id = None
        self._resort_delay_ms = 150

        # Идентификаторы сигналов для корректного отключения
        self._changed_handler_id = self.device.connect("changed", self.on_changed)
        self._closed_handler_id = self.device.connect("notify::closed", self._on_closed)

        # UI элементы
        self._create_ui_elements()

        # Запоминаем начальные состояния
        self.paired_on_init = device.paired
        self.connected_on_init = device.connected

        # Первичное обновление UI (принудительно)
        self._cached_connected = None  # Сбросить кеш для первого обновления
        self.device.emit("changed")

    def _create_ui_elements(self):
        """Создание UI элементов виджета"""
        device = self.device

        # Иконка состояния подключения
        self.connection_label = Label(
            name="bluetooth-connection",
            markup=icons.bluetooth_disconnected,
        )

        # Кнопка подключения/отключения
        self.connect_button = Button(
            name="bluetooth-connect",
            label="Connect",
            on_clicked=self._on_connect_clicked,
            style_classes=["connected"] if device.connected else None,
        )
        self.connect_button.set_tooltip_text(
            "Connect or Disconnect this Bluetooth device"
        )
        add_hover_cursor(self.connect_button)

        # Название устройства
        self.name_label = Label(
            label=device.name or "Unknown Device",  # ✅ Fallback для пустого имени
            h_expand=True,
            h_align="start",
            ellipsization="end",
            tooltip_text=device.address,
        )

        # Левая часть: иконка + имя + статус
        self.start_children = [
            Box(
                spacing=8,
                h_expand=True,
                h_align="fill",
                children=[
                    Image(
                        icon_name=(device.icon_name or "bluetooth") + "-symbolic",
                        size=16
                    ),
                    self.name_label,
                    self.connection_label,
                ],
            )
        ]

        # Правая часть: кнопки управления
        if device.paired:
            self.forget_button = Button(
                name="bluetooth-forget",
                label="Forget",
                tooltip_text="Remove this device from paired list",
                on_clicked=lambda *_: self._safe_remove_device(),
                style_classes=["destructive"],
            )
            add_hover_cursor(self.forget_button)
            self.end_children = Box(
                spacing=4,
                children=[self.connect_button, self.forget_button],
            )
        else:
            self.end_children = self.connect_button

    # ═════════════════════════════════════════════════════════════
    # Вспомогательные методы
    # ═════════════════════════════════════════════════════════════

    def _is_device_valid(self) -> bool:
        """Проверка, что устройство ещё активное и не закрыто"""
        return (
            hasattr(self, "device") 
            and self.device is not None
            and not getattr(self.device, "closed", False)
        )

    def _safe_remove_device(self):
        """Безопасное удаление устройства с обработкой ошибок"""
        if not self._is_device_valid():
            return

        try:
            self.device.remove()
        except Exception as e:
            print(f"⚠️ Error removing Bluetooth device: {e}")

    def _on_closed(self, *_):
        """Устройство закрыто — отцепить сигналы и уничтожить виджет"""
        self._cleanup()

        # Отложенное уничтожение для избежания конфликтов
        GLib.idle_add(self.destroy)

    def _cleanup(self):
        """Очистка ресурсов и отключение сигналов"""
        # Отменить отложенную пересортировку
        if self._resort_timeout_id is not None:
            GLib.source_remove(self._resort_timeout_id)
            self._resort_timeout_id = None

        # Отключить сигналы устройства
        if self._is_device_valid():
            try:
                if self._changed_handler_id:
                    self.device.disconnect(self._changed_handler_id)
                if self._closed_handler_id:
                    self.device.disconnect(self._closed_handler_id)
            except Exception:
                pass

        # Очистить ссылки
        self.device = None
        self.owner = None

    # ═════════════════════════════════════════════════════════════
    # Обработчики событий
    # ═════════════════════════════════════════════════════════════

    def _on_connect_clicked(self, *_):
        """Обработчик нажатия кнопки подключения"""
        if not self._is_device_valid():
            return

        try:
            # Fabric автоматически обрабатывает pairing/connecting
            self.device.set_connecting(not self.device.connected)
            # UI обновится через сигнал "changed"
        except Exception as e:
            print(f"⚠️ Error toggling Bluetooth connection: {e}")
            # Восстанавливаем кнопку в рабочее состояние
            self.connect_button.set_sensitive(True)

    # ═════════════════════════════════════════════════════════════
    # Обновление UI
    # ═════════════════════════════════════════════════════════════

    def on_changed(self, *_):
        """Обновить UI в зависимости от состояния устройства"""
        if not self._is_device_valid():
            return

        self._update_connection_icon()
        self._update_connect_button()
        self._handle_device_state_change()

    def _update_connection_icon(self):
        """Обновить иконку статуса подключения"""
        if not self._is_device_valid():
            return

        # ✅ Кеширование: обновлять только если изменилось
        connected = self.device.connected
        if self._cached_connected == connected:
            return

        self._cached_connected = connected
        icon = (
            icons.bluetooth_connected
            if connected
            else icons.bluetooth_disconnected
        )
        self.connection_label.set_markup(icon)

    def _update_connect_button(self):
        """Обновить кнопку подключения"""
        if not self._is_device_valid():
            return

        connecting = self.device.connecting
        connected = self.device.connected

        # Состояние "Connecting..."
        if connecting:
            if self._cached_connecting != True:
                self._cached_connecting = True
                self._cached_label = "Connecting..."
                self.connect_button.set_label("Connecting...")
                self.connect_button.set_sensitive(False)
            return

        # ✅ Кеширование: обновлять только если изменилось
        if self._cached_connecting == False and self._cached_connected == connected:
            return

        self._cached_connecting = False
        self._cached_connected = connected

        # Обычное состояние
        new_label = "Disconnect" if connected else "Connect"
        if self._cached_label != new_label:
            self._cached_label = new_label
            self.connect_button.set_label(new_label)

        self.connect_button.set_sensitive(True)

        # Стили
        if connected:
            self.connect_button.add_style_class("connected")
        else:
            self.connect_button.remove_style_class("connected")

    # ═════════════════════════════════════════════════════════════
    # Перестановка и сортировка
    # ═════════════════════════════════════════════════════════════

    def _handle_device_state_change(self):
        """Переставить виджет при изменении paired/connected"""
        if not self._is_device_valid():
            return

        if (
            self.device.paired != self.paired_on_init
            or self.device.connected != self.connected_on_init
        ):
            self._reposition_slot()
            self.paired_on_init = self.device.paired
            self.connected_on_init = self.device.connected

    def _reposition_slot(self):
        """Переместить слот в нужный box"""
        if not getattr(self, "owner", None) or not self._is_device_valid():
            return

        try:
            current_parent = self.get_parent()
            target_box = (
                self.owner.paired_box if self.device.paired
                else self.owner.available_box
            )

            # Переместить виджет если нужен другой box
            if current_parent is not target_box:
                if current_parent:
                    current_parent.remove(self)
                target_box.add(self)

                # ✅ Debounced resort: отложенная пересортировка
                self._schedule_resort(target_box)

        except RuntimeError:
            # Виджет или контейнер были уничтожены
            pass
        except Exception as e:
            print(f"⚠️ Repositioning error: {e}")

    def _schedule_resort(self, box: Box):
        """Запланировать пересортировку с debouncing"""
        # Отменить предыдущий таймер
        if self._resort_timeout_id is not None:
            GLib.source_remove(self._resort_timeout_id)

        # Запланировать новую пересортировку
        self._resort_timeout_id = GLib.timeout_add(
            self._resort_delay_ms,
            self._resort_callback,
            box
        )

    def _resort_callback(self, box: Box) -> bool:
        """Callback для отложенной пересортировки"""
        self._resort_box(box)
        self._resort_timeout_id = None
        return False  # Не повторять

    def _resort_box(self, box: Box):
        """
        Поддерживать порядок: подключённые сверху, далее по имени

        ✅ Оптимизация: сортировать только если порядок неправильный
        """
        if not box:
            return

        try:
            children = [
                c for c in box.get_children()
                if hasattr(c, "device") and c._is_device_valid()
            ]

            if not children:
                return

            # Сортировка: подключённые первыми, затем по алфавиту
            sorted_children = sorted(
                children,
                key=lambda child: (
                    not child.device.connected,  # False < True, подключённые сверху
                    getattr(child.device, "name", "").lower(),
                ),
            )

            # ✅ Проверка: нужна ли пересортировка?
            if children == sorted_children:
                return

            # Пересортировка
            for child in children:
                box.remove(child)

            for child in sorted_children:
                box.add(child)

        except Exception as e:
            print(f"⚠️ Resort error: {e}")


# ═════════════════════════════════════════════════════════════════
# Главный виджет Bluetooth
# ═════════════════════════════════════════════════════════════════

class BluetoothConnections(Box):
    """
    Главный виджет для управления Bluetooth подключениями

    Улучшения:
    - Кеширование виджетов статуса
    - Улучшенная обработка ошибок
    - Оптимизированные обновления UI
    """

    def __init__(self, widgets, **kwargs):
        super().__init__(
            name="bluetooth",
            spacing=4,
            orientation="vertical",
            **kwargs,
        )

        self.widgets = widgets
        self.buttons = self.widgets.buttons.bluetooth_button

        # Кеш статуса для избежания лишних обновлений
        self._cached_enabled = None
        self._cached_scanning = None

        # Ссылки на виджеты статуса
        self._init_status_widgets()

        # Bluetooth клиент
        self.client = BluetoothClient(on_device_added=self.on_device_added)

        # UI элементы
        self._create_ui()

        # Подключить сигналы
        self.client.connect("notify::enabled", lambda *_: self.update_status())
        self.client.connect("notify::scanning", lambda *_: self.update_scan_label())

        # Первичное обновление
        self.client.notify("scanning")
        self.client.notify("enabled")

    def _init_status_widgets(self):
        """Инициализация ссылок на виджеты статуса"""
        self.bt_status_text = self.buttons.bluetooth_status_text
        self.bt_status_button = self.buttons.bluetooth_status_button
        self.bt_icon = self.buttons.bluetooth_icon
        self.bt_label = self.buttons.bluetooth_label
        self.bt_menu_button = self.buttons.bluetooth_menu_button
        self.bt_menu_label = self.buttons.bluetooth_menu_label

        # ✅ Кешированный список виджетов статуса
        self._status_widgets_list = [
            self.bt_status_button,
            self.bt_status_text,
            self.bt_icon,
            self.bt_label,
            self.bt_menu_button,
            self.bt_menu_label,
        ]

    def _create_ui(self):
        """Создание UI элементов"""
        # Кнопка сканирования
        self.scan_label = Label(name="bluetooth-scan-label", markup=icons.radar)
        self.scan_button = Button(
            name="bluetooth-scan",
            child=self.scan_label,
            tooltip_text="Scan for Bluetooth devices",
            on_clicked=lambda *_: self._toggle_scan(),
        )
        add_hover_cursor(self.scan_button)

        # Кнопка назад
        self.back_button = Button(
            name="bluetooth-back",
            child=Label(name="bluetooth-back-label", markup=icons.chevron_left),
            on_clicked=lambda *_: self.widgets.show_notif(),
        )
        add_hover_cursor(self.back_button)

        # Контейнеры для устройств
        self.paired_box = Box(spacing=2, orientation="vertical")
        self.available_box = Box(spacing=2, orientation="vertical")

        # Контент
        content_box = Box(spacing=4, orientation="vertical")
        content_box.add(self.paired_box)
        content_box.add(Label(name="bluetooth-section", label="Available"))
        content_box.add(self.available_box)

        # Собираем виджет
        self.children = [
            CenterBox(
                name="bluetooth-header",
                start_children=self.back_button,
                center_children=Label(
                    name="bluetooth-text",
                    label="Bluetooth Devices",
                ),
                end_children=self.scan_button,
            ),
            ScrolledWindow(
                name="bluetooth-devices",
                min_content_size=(-1, -1),
                child=content_box,
                v_expand=True,
                propagate_width=False,
                propagate_height=False,
            ),
        ]

    # ═════════════════════════════════════════════════════════════
    # Обработчики событий
    # ═════════════════════════════════════════════════════════════

    def _toggle_scan(self):
        """Переключить сканирование с обработкой ошибок"""
        try:
            self.client.toggle_scan()
        except Exception as e:
            print(f"⚠️ Error toggling Bluetooth scan: {e}")

    def on_device_added(self, client: BluetoothClient, address: str):
        """Добавить новое устройство в список"""
        try:
            device = client.get_device(address)
            if not device:
                return

            # Создать слот для устройства
            slot = BluetoothDeviceSlot(device, owner=self)

            # Добавить в нужный box
            target_box = self.paired_box if device.paired else self.available_box
            target_box.add(slot)

        except Exception as e:
            print(f"⚠️ Error adding Bluetooth device: {e}")

    # ═════════════════════════════════════════════════════════════
    # Обновление UI
    # ═════════════════════════════════════════════════════════════

    def update_status(self):
        """
        Обновить статус Bluetooth (включен/выключен)

        ✅ Оптимизация: обновлять только при изменении
        """
        enabled = self.client.enabled

        # Кеширование: пропустить если не изменилось
        if self._cached_enabled == enabled:
            return

        self._cached_enabled = enabled

        if enabled:
            self.bt_status_text.set_label("Enabled")
            self.bt_icon.set_markup(icons.bluetooth)

            for widget in self._status_widgets_list:
                widget.remove_style_class("disabled")
        else:
            self.bt_status_text.set_label("Disabled")
            self.bt_icon.set_markup(icons.bluetooth_off)

            for widget in self._status_widgets_list:
                widget.add_style_class("disabled")

    def update_scan_label(self):
        """
        Обновить индикатор сканирования

        ✅ Оптимизация: обновлять только при изменении
        """
        scanning = self.client.scanning

        # Кеширование: пропустить если не изменилось
        if self._cached_scanning == scanning:
            return

        self._cached_scanning = scanning

        if scanning:
            self.scan_label.add_style_class("scanning")
            self.scan_button.add_style_class("scanning")
            self.scan_button.set_tooltip_text("Stop scanning for Bluetooth devices")
        else:
            self.scan_label.remove_style_class("scanning")
            self.scan_button.remove_style_class("scanning")
            self.scan_button.set_tooltip_text("Scan for Bluetooth devices")
