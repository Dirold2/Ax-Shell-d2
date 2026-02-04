from typing import Optional, Callable

from fabric.audio.service import Audio
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.circularprogressbar import CircularProgressBar
from fabric.widgets.eventbox import EventBox
from fabric.widgets.label import Label
from fabric.widgets.overlay import Overlay
from fabric.widgets.scale import Scale
from gi.repository import Gdk, GLib

import config.data as data
import modules.icons as icons
from services.brightness import Brightness


class BaseSlider(Scale):
    """Базовый класс для слайдеров с debouncing"""

    def __init__(
        self,
        name: str,
        style_class: str,
        debounce_timeout: int = 100,
        **kwargs
    ):
        super().__init__(
            name=name,
            orientation="h",
            h_expand=True,
            h_align="fill",
            has_origin=True,
            **kwargs,
        )
        self.add_style_class(style_class)
        self._pending_value: Optional[float] = None
        self._update_source_id: Optional[int] = None
        self._debounce_timeout = debounce_timeout
        self._updating_from_source = False

    def _apply_pending_change(self) -> bool:
        """Применить отложенное изменение значения"""
        raise NotImplementedError("Subclasses must implement _apply_pending_change")

    def _schedule_update(self, value: float):
        """Запланировать обновление с debouncing"""
        self._pending_value = value
        if self._update_source_id is not None:
            GLib.source_remove(self._update_source_id)
        self._update_source_id = GLib.timeout_add(
            self._debounce_timeout, self._apply_pending_change
        )

    def cleanup(self):
        """Очистить таймеры и ресурсы"""
        if self._update_source_id is not None:
            GLib.source_remove(self._update_source_id)
            self._update_source_id = None

    def destroy(self):
        """Переопределение destroy для корректной очистки"""
        self.cleanup()
        super().destroy()


class VolumeSlider(BaseSlider):
    """Слайдер громкости динамика"""

    def __init__(self, **kwargs):
        super().__init__(
            name="control-slider",
            style_class="vol",
            increments=(0.01, 0.1),
            **kwargs
        )
        self.audio = Audio()
        self.audio.connect("notify::speaker", self.on_new_speaker)
        if self.audio.speaker:
            self.audio.speaker.connect("changed", self.on_speaker_changed)
        self.connect("change-value", self.on_change_value)
        self.on_speaker_changed()

    def on_new_speaker(self, *args):
        """Обработчик подключения нового устройства воспроизведения"""
        if self.audio.speaker:
            self.audio.speaker.connect("changed", self.on_speaker_changed)
            self.on_speaker_changed()

    def on_change_value(self, widget, scroll, value):
        """Обработчик изменения значения слайдера"""
        if self._updating_from_source or not self.audio.speaker:
            return False
        self._schedule_update(value * 100)
        return False

    def _apply_pending_change(self) -> bool:
        """Применить изменение громкости"""
        if self._pending_value is not None and self.audio.speaker:
            self.audio.speaker.volume = self._pending_value
        self._pending_value = None
        self._update_source_id = None
        return False

    def on_speaker_changed(self, *_):
        """Обработчик изменения состояния динамика"""
        if not self.audio.speaker:
            return

        self._updating_from_source = True
        self.value = self.audio.speaker.volume / 100
        self._updating_from_source = False

        # Обновление стиля muted
        if self.audio.speaker.muted:
            self.add_style_class("muted")
        else:
            self.remove_style_class("muted")


class MicSlider(BaseSlider):
    """Слайдер громкости микрофона"""

    def __init__(self, **kwargs):
        super().__init__(
            name="control-slider",
            style_class="mic",
            increments=(0.01, 0.1),
            debounce_timeout=50,  # Быстрее для микрофона
            **kwargs
        )
        self.audio = Audio()
        self.audio.connect("notify::microphone", self.on_new_microphone)
        if self.audio.microphone:
            self.audio.microphone.connect("changed", self.on_microphone_changed)
        self.connect("change-value", self.on_change_value)
        self.on_microphone_changed()

    def on_new_microphone(self, *args):
        """Обработчик подключения нового микрофона"""
        if self.audio.microphone:
            self.audio.microphone.connect("changed", self.on_microphone_changed)
            self.on_microphone_changed()

    def on_change_value(self, widget, scroll, value):
        """Обработчик изменения значения слайдера"""
        if self._updating_from_source or not self.audio.microphone:
            return False
        # Мгновенное применение для микрофона
        self.audio.microphone.volume = value * 100
        return False

    def _apply_pending_change(self) -> bool:
        """Не используется для микрофона (мгновенное применение)"""
        return False

    def on_microphone_changed(self, *_):
        """Обработчик изменения состояния микрофона"""
        if not self.audio.microphone:
            return

        self._updating_from_source = True
        self.value = self.audio.microphone.volume / 100
        self._updating_from_source = False

        if self.audio.microphone.muted:
            self.add_style_class("muted")
        else:
            self.remove_style_class("muted")


class BrightnessSlider(BaseSlider):
    """Слайдер яркости экрана"""

    def __init__(self, **kwargs):
        super().__init__(
            name="control-slider",
            style_class="brightness",
            increments=(5, 10),
            **kwargs
        )
        self.client = Brightness.get_initial()
        if self.client.screen_brightness == -1:
            self.destroy()
            return

        self.set_range(0, self.client.max_screen)
        self.set_value(self.client.screen_brightness)
        self.connect("change-value", self.on_scale_move)
        self.client.connect("screen", self.on_brightness_changed)
        self._update_tooltip()

    def on_scale_move(self, widget, scroll, moved_pos):
        """Обработчик перемещения слайдера"""
        if self._updating_from_source:
            return False
        self._schedule_update(moved_pos)
        return False

    def _apply_pending_change(self) -> bool:
        """Применить изменение яркости"""
        if self._pending_value is not None:
            value_to_set = self._pending_value
            self._pending_value = None
            if value_to_set != self.client.screen_brightness:
                self.client.screen_brightness = value_to_set
        self._update_source_id = None
        return False

    def on_brightness_changed(self, client, _):
        """Обработчик изменения яркости"""
        self._updating_from_source = True
        self.set_value(self.client.screen_brightness)
        self._updating_from_source = False
        self._update_tooltip()

    def _update_tooltip(self):
        """Обновить tooltip с процентами яркости"""
        if self.client.max_screen > 0:
            percentage = int((self.client.screen_brightness / self.client.max_screen) * 100)
            self.set_tooltip_text(f"{percentage}%")


class BaseSmallControl(Box):
    """Базовый класс для компактных элементов управления с прогресс-баром"""

    def __init__(
        self,
        name: str,
        button_name: str,
        icon_markup: str,
        on_button_click: Optional[Callable] = None,
        **kwargs
    ):
        super().__init__(name=name, **kwargs)
        self.progress_bar = CircularProgressBar(
            name=button_name,
            size=28,
            line_width=2,
            start_angle=150,
            end_angle=390,
        )
        self.icon_label = Label(
            name=f"{button_name.split('-')[-1]}-label",
            markup=icon_markup,
        )

        # Создаём Button с on_clicked только если callback предоставлен
        if on_button_click is not None:
            self.button = Button(
                child=self.icon_label,
                on_clicked=on_button_click
            )
        else:
            self.button = Button(child=self.icon_label)

        self.event_box = EventBox(
            events=["scroll", "smooth-scroll"],
            child=Overlay(child=self.progress_bar, overlays=self.button),
        )
        self.event_box.connect("scroll-event", self.on_scroll)
        self.add(self.event_box)
        self.add_events(Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK)

        self._pending_value: Optional[float] = None
        self._update_source_id: Optional[int] = None
        self._updating_from_source = False
        self._debounce_timeout = 100

    def on_scroll(self, widget, event):
        """Обработчик прокрутки колеса мыши"""
        raise NotImplementedError("Subclasses must implement on_scroll")

    def _schedule_update(self, new_value: float):
        """Запланировать обновление с debouncing"""
        self._pending_value = new_value
        if self._update_source_id is not None:
            GLib.source_remove(self._update_source_id)
        self._update_source_id = GLib.timeout_add(
            self._debounce_timeout, self._update_callback
        )

    def _update_callback(self) -> bool:
        """Callback для применения изменений"""
        raise NotImplementedError("Subclasses must implement _update_callback")

    def cleanup(self):
        """Очистить таймеры"""
        if self._update_source_id is not None:
            GLib.source_remove(self._update_source_id)
            self._update_source_id = None

    def destroy(self):
        """Переопределение destroy для корректной очистки"""
        self.cleanup()
        super().destroy()


class BrightnessSmall(BaseSmallControl):
    """Компактный элемент управления яркостью"""

    def __init__(self, **kwargs):
        super().__init__(
            name="button-bar-brightness",
            button_name="button-brightness",
            icon_markup=icons.brightness_high,
            on_button_click=None,
            **kwargs
        )
        self._debounce_timeout = 10  # Средняя скорость
        self.brightness = Brightness.get_initial()
        if self.brightness.screen_brightness == -1:
            self.destroy()
            return

        self.brightness.connect("screen", self.on_brightness_changed)
        self.on_brightness_changed()

    def on_scroll(self, widget, event):
        """Обработчик прокрутки для изменения яркости"""
        if self._updating_from_source:  # ← Как в BrightnessSlider
            return
        
        if self.brightness.max_screen == -1:
            return

        step_size = 5
        current = self.brightness.screen_brightness
        new_brightness = None

        if event.direction == Gdk.ScrollDirection.SMOOTH:
            if event.delta_y < 0:
                new_brightness = min(current + step_size, self.brightness.max_screen)
            elif event.delta_y > 0:
                new_brightness = max(current - step_size, 0)
        elif event.direction == Gdk.ScrollDirection.UP:
            new_brightness = min(current + step_size, self.brightness.max_screen)
        elif event.direction == Gdk.ScrollDirection.DOWN:
            new_brightness = max(current - step_size, 0)

        if new_brightness is not None:
            self._schedule_update(new_brightness)

    def _update_callback(self) -> bool:
        """Применить изменение яркости"""
        if self._pending_value is not None:
            value_to_set = self._pending_value
            self._pending_value = None
            if value_to_set != self.brightness.screen_brightness:
                self._updating_from_source = True  # ← ВАЖНО: добавить флаг
                self.brightness.screen_brightness = value_to_set
                self._updating_from_source = False
        self._update_source_id = None
        return False

    def on_brightness_changed(self, *args):
        """Обработчик изменения яркости"""
        if self.brightness.max_screen == -1:
            return

        normalized = (
            self.brightness.screen_brightness / self.brightness.max_screen
            if self.brightness.max_screen > 0 else 0
        )

        self._updating_from_source = True  # ← Как в BrightnessSlider
        self.progress_bar.value = normalized
        self._updating_from_source = False

        percentage = int(normalized * 100)

        # Обновление иконки в зависимости от уровня яркости
        if percentage >= 75:
            self.icon_label.set_markup(icons.brightness_high)
        elif percentage >= 24:
            self.icon_label.set_markup(icons.brightness_medium)
        else:
            self.icon_label.set_markup(icons.brightness_low)

        self.set_tooltip_text(f"{percentage}%")


class VolumeSmall(BaseSmallControl):
    """Компактный элемент управления громкостью"""

    def __init__(self, **kwargs):
        self.audio = Audio()
        super().__init__(
            name="button-bar-vol",
            button_name="button-volume",
            icon_markup=icons.vol_high,
            on_button_click=self.toggle_mute,
            **kwargs
        )
        self.audio.connect("notify::speaker", self.on_new_speaker)
        if self.audio.speaker:
            self.audio.speaker.connect("changed", self.on_speaker_changed)
        self.on_speaker_changed()

    def on_new_speaker(self, *args):
        """Обработчик подключения нового устройства"""
        if self.audio.speaker:
            self.audio.speaker.connect("changed", self.on_speaker_changed)
            self.on_speaker_changed()

    def toggle_mute(self, event):
        """Переключить mute"""
        if self.audio.speaker:
            self.audio.speaker.muted = not self.audio.speaker.muted

    def on_scroll(self, widget, event):
        """Обработчик прокрутки для изменения громкости"""
        if not self.audio.speaker:
            return

        if event.direction == Gdk.ScrollDirection.SMOOTH:
            if abs(event.delta_y) > 0:
                self.audio.speaker.volume -= event.delta_y * 5
            elif abs(event.delta_x) > 0:
                self.audio.speaker.volume += event.delta_x * 5

    def _update_callback(self) -> bool:
        """Не используется (мгновенное применение)"""
        return False

    def on_speaker_changed(self, *_):
        """Обработчик изменения состояния динамика"""
        if not self.audio.speaker:
            return

        # Выбор иконок в зависимости от типа устройства
        if "bluetooth" in self.audio.speaker.icon_name:
            icons_set = {
                "high": icons.bluetooth_connected,
                "medium": icons.bluetooth,
                "mute": icons.bluetooth_off,
                "off": icons.bluetooth_disconnected
            }
        else:
            icons_set = {
                "high": icons.vol_high,
                "medium": icons.vol_medium,
                "mute": icons.vol_off,
                "off": icons.vol_mute
            }

        self.progress_bar.value = self.audio.speaker.volume / 100

        if self.audio.speaker.muted:
            self.icon_label.set_markup(icons_set["mute"])
            self.progress_bar.add_style_class("muted")
            self.icon_label.add_style_class("muted")
            self.set_tooltip_text("Muted")
        else:
            self.progress_bar.remove_style_class("muted")
            self.icon_label.remove_style_class("muted")
            volume = self.audio.speaker.volume
            if volume > 74:
                self.icon_label.set_markup(icons_set["high"])
            elif volume > 0:
                self.icon_label.set_markup(icons_set["medium"])
            else:
                self.icon_label.set_markup(icons_set["off"])
            self.set_tooltip_text(f"{round(volume)}%")


class MicSmall(BaseSmallControl):
    """Компактный элемент управления микрофоном"""

    def __init__(self, **kwargs):
        self.audio = Audio()
        super().__init__(
            name="button-bar-mic",
            button_name="button-mic",
            icon_markup=icons.mic,
            on_button_click=self.toggle_mute,
            **kwargs
        )
        self.audio.connect("notify::microphone", self.on_new_microphone)
        if self.audio.microphone:
            self.audio.microphone.connect("changed", self.on_microphone_changed)
        self.on_microphone_changed()

    def on_new_microphone(self, *args):
        """Обработчик подключения нового микрофона"""
        if self.audio.microphone:
            self.audio.microphone.connect("changed", self.on_microphone_changed)
            self.on_microphone_changed()

    def toggle_mute(self, event):
        """Переключить mute"""
        if self.audio.microphone:
            self.audio.microphone.muted = not self.audio.microphone.muted

    def on_scroll(self, widget, event):
        """Обработчик прокрутки для изменения громкости"""
        if not self.audio.microphone:
            return

        if event.direction == Gdk.ScrollDirection.SMOOTH:
            if abs(event.delta_y) > 0:
                self.audio.microphone.volume -= event.delta_y * 5
            elif abs(event.delta_x) > 0:
                self.audio.microphone.volume += event.delta_x * 5

    def _update_callback(self) -> bool:
        """Не используется (мгновенное применение)"""
        return False

    def on_microphone_changed(self, *_):
        """Обработчик изменения состояния микрофона"""
        if not self.audio.microphone:
            return

        if self.audio.microphone.muted:
            self.icon_label.set_markup(icons.mic_mute)
            self.progress_bar.add_style_class("muted")
            self.icon_label.add_style_class("muted")
            self.set_tooltip_text("Muted")
        else:
            self.progress_bar.remove_style_class("muted")
            self.icon_label.remove_style_class("muted")
            self.progress_bar.value = self.audio.microphone.volume / 100
            volume = self.audio.microphone.volume
            if volume >= 1:
                self.icon_label.set_markup(icons.mic)
            else:
                self.icon_label.set_markup(icons.mic_mute)
            self.set_tooltip_text(f"{round(volume)}%")


class BaseIcon(Box):
    """Базовый класс для иконок с прокруткой"""

    def __init__(
        self,
        name: str,
        label_name: str,
        icon_markup: str,
        on_button_click: Optional[Callable] = None,
        **kwargs
    ):
        super().__init__(name=name, **kwargs)
        self.icon_label = Label(
            name=label_name,
            markup=icon_markup,
            h_align="center",
            v_align="center",
            h_expand=True,
            v_expand=True,
        )

        # Создаём Button с on_clicked только если callback предоставлен
        button_kwargs = {
            "child": self.icon_label,
            "h_align": "center",
            "v_align": "center",
            "h_expand": True,
            "v_expand": True,
        }
        if on_button_click is not None:
            button_kwargs["on_clicked"] = on_button_click
        
        self.button = Button(**button_kwargs)

        self.event_box = EventBox(
            events=["scroll", "smooth-scroll"],
            child=self.button,
            h_align="center",
            v_align="center",
            h_expand=True,
            v_expand=True,
        )
        self.event_box.connect("scroll-event", self.on_scroll)
        self.add(self.event_box)
        self.add_events(Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK)

        self._pending_value: Optional[float] = None
        self._update_source_id: Optional[int] = None
        self._updating_from_source = False

    def on_scroll(self, widget, event):
        """Обработчик прокрутки"""
        raise NotImplementedError("Subclasses must implement on_scroll")

    def _handle_scroll_event(
        self,
        event,
        current_value: float,
        max_value: float = 100,
        step: int = 5
    ) -> Optional[float]:
        """Общая логика обработки scroll события"""
        if event.direction == Gdk.ScrollDirection.SMOOTH:
            if event.delta_y < 0:
                return min(current_value + step, max_value)
            elif event.delta_y > 0:
                return max(current_value - step, 0)
        else:
            if event.direction == Gdk.ScrollDirection.UP:
                return min(current_value + step, max_value)
            elif event.direction == Gdk.ScrollDirection.DOWN:
                return max(current_value - step, 0)
        return None

    def _schedule_update(self, new_value: float):
        """Запланировать обновление"""
        self._pending_value = new_value
        if self._update_source_id is not None:
            GLib.source_remove(self._update_source_id)
        self._update_source_id = GLib.timeout_add(100, self._update_callback)

    def _update_callback(self) -> bool:
        """Callback для применения изменений"""
        raise NotImplementedError("Subclasses must implement _update_callback")

    def cleanup(self):
        """Очистить таймеры"""
        if self._update_source_id is not None:
            GLib.source_remove(self._update_source_id)
            self._update_source_id = None

    def destroy(self):
        """Переопределение destroy для корректной очистки"""
        self.cleanup()
        super().destroy()


class BrightnessIcon(BaseIcon):
    """Иконка яркости с управлением через прокрутку"""

    def __init__(self, **kwargs):
        super().__init__(
            name="brightness-icon",
            label_name="brightness-label-dash",
            icon_markup=icons.brightness_high,
            on_button_click=None,  # Нет callback для кнопки яркости
            **kwargs
        )
        self.brightness = Brightness.get_initial()
        if self.brightness.screen_brightness == -1:
            self.destroy()
            return

        self.brightness.connect("screen", self.on_brightness_changed)
        self.on_brightness_changed()

    def on_scroll(self, widget, event):
        """Обработчик прокрутки для изменения яркости"""
        if self.brightness.max_screen == -1:
            return

        new_value = self._handle_scroll_event(
            event,
            self.brightness.screen_brightness,
            self.brightness.max_screen
        )
        if new_value is not None:
            self._schedule_update(new_value)

    def _update_callback(self) -> bool:
        """Применить изменение яркости"""
        if (self._pending_value is not None
                and self._pending_value != self.brightness.screen_brightness):
            self.brightness.screen_brightness = self._pending_value
        self._pending_value = None
        self._update_source_id = None
        return False

    def on_brightness_changed(self, *args):
        """Обработчик изменения яркости"""
        if self.brightness.max_screen == -1:
            return

        self._updating_from_source = True
        normalized = self.brightness.screen_brightness / self.brightness.max_screen
        percentage = int(normalized * 100)

        # Обновление иконки
        if percentage >= 75:
            self.icon_label.set_markup(icons.brightness_high)
        elif percentage >= 24:
            self.icon_label.set_markup(icons.brightness_medium)
        else:
            self.icon_label.set_markup(icons.brightness_low)

        self.set_tooltip_text(f"{percentage}%")
        self._updating_from_source = False


class VolumeIcon(BaseIcon):
    """Иконка громкости с управлением через прокрутку"""

    def __init__(self, **kwargs):
        self.audio = Audio()
        super().__init__(
            name="vol-icon",
            label_name="vol-label-dash",
            icon_markup=icons.vol_high,
            on_button_click=self.toggle_mute,
            **kwargs
        )
        self._periodic_update_source_id: Optional[int] = None
        self.audio.connect("notify::speaker", self.on_new_speaker)
        if self.audio.speaker:
            self.audio.speaker.connect("changed", self.on_speaker_changed)
            self._periodic_update_source_id = GLib.timeout_add_seconds(
                2, self.update_device_icon
            )
        self.on_speaker_changed()

    def on_scroll(self, widget, event):
        """Обработчик прокрутки для изменения громкости"""
        if not self.audio.speaker:
            return

        new_value = self._handle_scroll_event(event, self.audio.speaker.volume)
        if new_value is not None:
            self._schedule_update(new_value)

    def _update_callback(self) -> bool:
        """Применить изменение громкости"""
        if (self._pending_value is not None
                and self.audio.speaker
                and self._pending_value != self.audio.speaker.volume):
            self.audio.speaker.volume = self._pending_value
        self._pending_value = None
        self._update_source_id = None
        return False

    def on_new_speaker(self, *args):
        """Обработчик подключения нового устройства"""
        if self.audio.speaker:
            self.audio.speaker.connect("changed", self.on_speaker_changed)
            self.on_speaker_changed()

    def toggle_mute(self, event):
        """Переключить mute"""
        if self.audio.speaker:
            self.audio.speaker.muted = not self.audio.speaker.muted

    def on_speaker_changed(self, *_):
        """Обработчик изменения состояния динамика"""
        if not self.audio.speaker:
            self.icon_label.set_markup(icons.vol_off)
            self._remove_muted_styles()
            self.set_tooltip_text("No audio device")
            return

        if self.audio.speaker.muted:
            self.icon_label.set_markup(icons.headphones)
            self._apply_muted_styles()
            self.set_tooltip_text("Muted")
        else:
            self._remove_muted_styles()
            self.update_device_icon()
            self.set_tooltip_text(f"{round(self.audio.speaker.volume)}%")

    def _apply_muted_styles(self):
        """Применить стили для muted состояния"""
        self.add_style_class("muted")
        self.icon_label.add_style_class("muted")
        self.button.add_style_class("muted")

    def _remove_muted_styles(self):
        """Удалить стили muted состояния"""
        self.remove_style_class("muted")
        self.icon_label.remove_style_class("muted")
        self.button.remove_style_class("muted")

    def update_device_icon(self) -> bool:
        """Обновить иконку в зависимости от типа устройства"""
        if not self.audio.speaker or self.audio.speaker.muted:
            return True

        try:
            # Можно расширить для других типов устройств
            icon = icons.headphones  # По умолчанию
            self.icon_label.set_markup(icon)
        except AttributeError:
            self.icon_label.set_markup(icons.headphones)
        return True

    def cleanup(self):
        """Очистить все таймеры"""
        super().cleanup()
        if self._periodic_update_source_id is not None:
            GLib.source_remove(self._periodic_update_source_id)
            self._periodic_update_source_id = None

    def destroy(self):
        """Переопределение destroy"""
        self.cleanup()
        super().destroy()


class MicIcon(BaseIcon):
    """Иконка микрофона с управлением через прокрутку"""

    def __init__(self, **kwargs):
        self.audio = Audio()
        super().__init__(
            name="mic-icon",
            label_name="mic-label-dash",
            icon_markup=icons.mic,
            on_button_click=self.toggle_mute,
            **kwargs
        )
        self.audio.connect("notify::microphone", self.on_new_microphone)
        if self.audio.microphone:
            self.audio.microphone.connect("changed", self.on_microphone_changed)
        self.on_microphone_changed()

    def on_scroll(self, widget, event):
        """Обработчик прокрутки для изменения громкости"""
        if not self.audio.microphone:
            return

        new_value = self._handle_scroll_event(event, self.audio.microphone.volume)
        if new_value is not None:
            self._schedule_update(new_value)

    def _update_callback(self) -> bool:
        """Применить изменение громкости"""
        if (self._pending_value is not None
                and self.audio.microphone
                and self._pending_value != self.audio.microphone.volume):
            self.audio.microphone.volume = self._pending_value
        self._pending_value = None
        self._update_source_id = None
        return False

    def on_new_microphone(self, *args):
        """Обработчик подключения нового микрофона"""
        if self.audio.microphone:
            self.audio.microphone.connect("changed", self.on_microphone_changed)
            self.on_microphone_changed()

    def toggle_mute(self, event):
        """Переключить mute"""
        if self.audio.microphone:
            self.audio.microphone.muted = not self.audio.microphone.muted

    def on_microphone_changed(self, *_):
        """Обработчик изменения состояния микрофона"""
        if not self.audio.microphone:
            return

        if self.audio.microphone.muted:
            self.icon_label.set_markup(icons.mic_mute)
            self.add_style_class("muted")
            self.icon_label.add_style_class("muted")
            self.set_tooltip_text("Muted")
        else:
            self.remove_style_class("muted")
            self.icon_label.remove_style_class("muted")
            volume = self.audio.microphone.volume
            if volume >= 1:
                self.icon_label.set_markup(icons.mic)
            else:
                self.icon_label.set_markup(icons.mic_mute)
            self.set_tooltip_text(f"{round(volume)}%")


class ControlSliders(Box):
    """Контейнер со слайдерами управления"""

    def __init__(self, **kwargs):
        super().__init__(
            name="control-sliders",
            orientation="h",
            spacing=8,
            **kwargs,
        )

        # Добавляем яркость если доступна
        brightness = Brightness.get_initial()
        if brightness.screen_brightness != -1:
            brightness_row = Box(
                orientation="h", spacing=0, h_expand=True, h_align="fill"
            )
            brightness_row.add(BrightnessIcon())
            brightness_row.add(BrightnessSlider())
            self.add(brightness_row)

        # Добавляем громкость
        volume_row = Box(orientation="h", spacing=0, h_expand=True, h_align="fill")
        volume_row.add(VolumeIcon())
        volume_row.add(VolumeSlider())
        self.add(volume_row)

        # Добавляем микрофон
        mic_row = Box(orientation="h", spacing=0, h_expand=True, h_align="fill")
        mic_row.add(MicIcon())
        mic_row.add(MicSlider())
        self.add(mic_row)

        self.show_all()


class ControlSmall(Box):
    """Компактный контейнер с элементами управления"""

    def __init__(self, **kwargs):
        brightness = Brightness.get_initial()
        children = []
        
        if brightness.screen_brightness != -1:
            children.append(BrightnessSmall())
        children.extend([VolumeSmall(), MicSmall()])

        super().__init__(
            name="control-small",
            orientation="h" if not data.VERTICAL else "v",
            spacing=4,
            children=children,
            **kwargs,
        )
        self.show_all()
