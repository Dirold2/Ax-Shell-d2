import math
import gi
from typing import Optional

from services.audio import Audio
from fabric.widgets.box import Box
from fabric.widgets.label import Label
from fabric.widgets.scale import Scale
from fabric.widgets.scrolledwindow import ScrolledWindow
from gi.repository import GLib

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

import config.data as data

vertical_mode = (
    data.PANEL_THEME == "Panel"
    and (
        data.BAR_POSITION in ["Left", "Right"]
        or data.PANEL_POSITION in ["Start", "End"]
    )
)


class MixerSlider(Scale):
    def __init__(self, stream, **kwargs):
        super().__init__(
            name="control-slider",
            orientation="h",
            h_expand=True,
            h_align="fill",
            has_origin=True,
            increments=(0.01, 0.1),
            style_classes=["no-icon"],
            **kwargs,
        )

        self.stream = stream
        self._updating_from_stream = False
        self._stream_changed_id = stream.connect("changed", self.on_stream_changed)
        self._debounce_timeout_id: Optional[int] = None

        # Начальное значение (0–1) с клампом
        initial = self._get_normalized_volume()
        adj = self.get_adjustment()
        if isinstance(adj, Gtk.Adjustment):
            self.set_value(initial)

        self.set_size_request(-1, 30)
        self.connect("value-changed", self.on_value_changed)
        self.connect("destroy", self._on_destroy)

        # Стили по типу стрима
        self._apply_stream_styles()
        self._update_tooltip()
        self.update_muted_state()

    def _get_normalized_volume(self) -> float:
        """Получить нормализованную громкость (0-1)"""
        vol = getattr(self.stream, "volume", 0) or 0
        return max(0.0, min(1.0, vol / 100.0))

    def _get_volume_percent(self) -> float:
        """Получить громкость в процентах"""
        return getattr(self.stream, "volume", 0) or 0

    def _apply_stream_styles(self):
        """Применить стили в зависимости от типа стрима"""
        stream_type = getattr(self.stream, "type", "") or ""
        st_lower = stream_type.lower()
        if "microphone" in st_lower or "input" in st_lower:
            self.add_style_class("mic")
        else:
            self.add_style_class("vol")

    def _update_tooltip(self):
        """Обновить tooltip с текущей громкостью"""
        vol = self._get_volume_percent()
        self.set_tooltip_text(f"{vol:.0f}%")

    def _on_destroy(self, *_):
        """Очистка ресурсов при уничтожении виджета"""
        if self._debounce_timeout_id:
            GLib.source_remove(self._debounce_timeout_id)
            self._debounce_timeout_id = None

        if self.stream and self._stream_changed_id:
            try:
                self.stream.disconnect(self._stream_changed_id)
            except Exception:
                pass
            self._stream_changed_id = None

    def _apply_volume_change(self) -> bool:
        """Применить изменение громкости с debouncing"""
        if not self.stream:
            return False

        val = max(0.0, min(1.0, self.value))
        self.stream.volume = val * 100.0
        self._update_tooltip()
        self._debounce_timeout_id = None
        return False

    def on_value_changed(self, _):
        """Обработчик изменения значения слайдера"""
        if self._updating_from_stream or not self.stream:
            return

        # Debouncing: отложенное применение изменений
        if self._debounce_timeout_id:
            GLib.source_remove(self._debounce_timeout_id)

        self._debounce_timeout_id = GLib.timeout_add(50, self._apply_volume_change)

    def on_stream_changed(self, stream):
        """Обработчик изменения состояния стрима"""
        self._updating_from_stream = True
        try:
            adj = self.get_adjustment()
            if not isinstance(adj, Gtk.Adjustment):
                return

            new_val = self._get_normalized_volume()
            self.set_value(new_val)
            self._update_tooltip()
            self.update_muted_state()
        finally:
            self._updating_from_stream = False

    def update_muted_state(self):
        """Обновить визуальное состояние muted"""
        muted = bool(getattr(self.stream, "muted", False))
        if muted:
            self.add_style_class("muted")
        else:
            self.remove_style_class("muted")


class MixerSection(Box):
    def __init__(self, title: str, **kwargs):
        super().__init__(
            name="mixer-section",
            orientation="v",
            spacing=8,
            h_expand=True,
            v_expand=False,
            **kwargs,
        )

        self.title_label = Label(
            name="mixer-section-title",
            label=title,
            h_expand=True,
            h_align="fill",
        )

        self.content_box = Box(
            name="mixer-content",
            orientation="v",
            spacing=8,
            h_expand=True,
            v_expand=False,
        )

        self.add(self.title_label)
        self.add(self.content_box)

    def _get_stream_label_text(self, stream) -> str:
        """Получить текст метки для стрима"""
        stream_type = getattr(stream, "type", "") or ""
        desc = getattr(stream, "description", "") or ""
        name = getattr(stream, "name", "") or ""

        if "application" in stream_type.lower():
            # Для приложений пытаемся совместить name и description
            if name and desc and name != desc:
                return f"{name} — {desc}"
            elif name:
                return name

        return desc or name or "Unknown"

    def _create_stream_widget(self, stream) -> Box:
        """Создать виджет для отдельного стрима"""
        stream_container = Box(
            orientation="v",
            spacing=4,
            h_expand=True,
            v_expand=False,
        )

        vol = getattr(stream, "volume", 0) or 0
        display_name = getattr(stream, "display_name", None) or self._get_stream_label_text(stream)

        label = Label(
            name="mixer-stream-label",
            label=f"[{math.ceil(vol)}%] {display_name}",
            h_expand=True,
            h_align="start",
            v_align="center",
            ellipsization="end",
            max_chars_width=45,
            height_request=20,
        )

        slider = MixerSlider(stream)

        # Обновление метки при изменении громкости
        def update_label(*_):
            new_vol = getattr(stream, "volume", 0) or 0
            label.set_label(f"[{math.ceil(new_vol)}%] {display_name}")

        stream.connect("changed", update_label)

        stream_container.add(label)
        stream_container.add(slider)

        return stream_container

    def update_streams(self, streams):
        """Обновить список стримов"""
        # Чистим старые элементы с правильным освобождением памяти
        for child in list(self.content_box.get_children()):
            self.content_box.remove(child)
            child.destroy()

        # Добавляем новые стримы
        for stream in streams:
            stream_widget = self._create_stream_widget(stream)
            self.content_box.add(stream_widget)

        self.content_box.show_all()


class Mixer(Box):
    def __init__(self, **kwargs):
        super().__init__(
            name="mixer",
            orientation="v",
            spacing=8,
            h_expand=True,
            v_expand=True,
            **kwargs,
        )

        try:
            self.audio = Audio()
        except Exception as e:
            error_label = Label(
                label=f"Audio service unavailable: {str(e)}",
                h_align="center",
                v_align="center",
                h_expand=True,
                v_expand=True,
            )
            self.add(error_label)
            return

        self._setup_ui()
        self._connect_signals()
        self.update_mixer()
        self.show_all()

    def _setup_ui(self):
        """Настроить UI компоненты"""
        self.main_container = Box(
            orientation="h" if not vertical_mode else "v",
            spacing=8,
            h_expand=True,
            v_expand=True,
        )
        self.main_container.set_homogeneous(True)

        # Outputs
        self.outputs_scrolled = ScrolledWindow(
            name="outputs-scrolled",
            h_expand=True,
            v_expand=True,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
        )
        self.outputs_section = MixerSection("Outputs")
        self.outputs_scrolled.add(self.outputs_section)

        # Inputs
        self.inputs_scrolled = ScrolledWindow(
            name="inputs-scrolled",
            h_expand=True,
            v_expand=True,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
        )
        self.inputs_section = MixerSection("Inputs")
        self.inputs_scrolled.add(self.inputs_section)

        self.main_container.add(self.outputs_scrolled)
        self.main_container.add(self.inputs_scrolled)
        self.add(self.main_container)

    def _connect_signals(self):
        """Подключить сигналы аудио-сервиса"""
        self.audio.connect("changed", self.on_audio_changed)
        self.audio.connect("stream-added", self.on_audio_changed)
        self.audio.connect("stream-removed", self.on_audio_changed)

    def on_audio_changed(self, *_):
        """Обработчик изменений в аудио-сервисе"""
        self.update_mixer()

    def update_mixer(self):
        """Обновить состояние микшера"""
        outputs: list = []
        inputs: list = []

        # Собираем outputs
        if getattr(self.audio, "speaker", None):
            outputs.append(self.audio.speaker)
        outputs.extend(getattr(self.audio, "applications", []) or [])

        # Собираем inputs
        if getattr(self.audio, "microphone", None):
            inputs.append(self.audio.microphone)
        inputs.extend(getattr(self.audio, "recorders", []) or [])

        self.outputs_section.update_streams(outputs)
        self.inputs_section.update_streams(inputs)
