import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")

from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

from fabric.utils import get_relative_path
from fabric.widgets.box import Box
from fabric.widgets.image import Image
from fabric.widgets.label import Label
from fabric.widgets.stack import Stack

import config.data as data
import modules.icons as icons

from modules.kanban import Kanban
from modules.mixer import Mixer
from modules.pins import Pins
from modules.wallpapers import WallpaperSelector
from modules.widgets import Widgets


class Dashboard(Box):
    def __init__(self, notch, **kwargs):
        super().__init__(
            name="dashboard",
            orientation="v",
            spacing=8,
            h_align="center",
            v_align="center",
            h_expand=True,
            visible=True,
            all_visible=True,
            **kwargs,
        )

        self.notch = notch

        # Основные разделы
        self.widgets = Widgets(notch=self.notch)
        self.pins = Pins()
        self.kanban = Kanban()
        self.wallpapers = WallpaperSelector()
        self.mixer = Mixer()

        # Стек основного содержимого
        self.stack = Stack(
            name="stack",
            transition_type="slide-left-right",
            transition_duration=500,
            v_expand=True,
            v_align="fill",
            h_expand=True,
            h_align="fill",
        )
        self.stack.set_homogeneous(False)

        # Свитчер вкладок
        self.switcher = Gtk.StackSwitcher(
            name="switcher",
            spacing=8,
        )

        # Добавляем страницы в стек
        self.stack.add_titled(self.widgets, "widgets", "Widgets")
        self.stack.add_titled(self.pins, "pins", "Pins")
        self.stack.add_titled(self.kanban, "kanban", "Kanban")
        self.stack.add_titled(self.wallpapers, "wallpapers", "Wallpapers")
        self.stack.add_titled(self.mixer, "mixer", "Mixer")

        # Привязываем свитчер к стеку
        self.switcher.set_stack(self.stack)
        self.switcher.set_hexpand(True)
        self.switcher.set_homogeneous(True)
        self.switcher.set_can_focus(True)

        # Реакция на смену видимого ребёнка (например, авто‑фокус поиска обоев)
        self.stack.connect("notify::visible-child", self.on_visible_child_changed)

        # Компоновка
        self.add(self.switcher)
        self.add(self.stack)

        # Для вертикальной панели — заменить текст свитчера иконками
        if (
            data.PANEL_THEME == "Panel"
            and (
                data.BAR_POSITION in ["Left", "Right"]
                or data.PANEL_POSITION in ["Start", "End"]
            )
        ):
            GLib.idle_add(self._setup_switcher_icons)

        # Закрытие по правому клику в свободном месте
        self.connect("button-release-event", self._on_button_release)

        self.show_all()

        # Удобная мапа секций по имени
        self._sections = {
            "widgets": self.widgets,
            "pins": self.pins,
            "kanban": self.kanban,
            "wallpapers": self.wallpapers,
            "mixer": self.mixer,
        }

    # -----------------------
    # Внутренние методы
    # -----------------------

    def _setup_switcher_icons(self):
        icon_details_map = {
            "Widgets": {"icon": icons.widgets, "name": "widgets"},
            "Pins": {"icon": icons.pins, "name": "pins"},
            "Kanban": {"icon": icons.kanban, "name": "kanban"},
            "Wallpapers": {"icon": icons.wallpapers, "name": "wallpapers"},
            "Mixer": {"icon": icons.speaker, "name": "mixer"},
        }

        for btn in self.switcher.get_children():
            if not isinstance(btn, Gtk.ToggleButton):
                continue

            original_label = None
            for child in btn.get_children():
                if isinstance(child, Gtk.Label):
                    original_label = child
                    break

            if not original_label:
                continue

            label_text = original_label.get_text()
            details = icon_details_map.get(label_text)
            if not details:
                continue

            icon_markup = details["icon"]
            css_name_suffix = details["name"]

            btn.remove(original_label)

            new_icon_label = Label(
                name=f"switcher-icon-{css_name_suffix}",
                markup=icon_markup,
            )
            btn.add(new_icon_label)
            new_icon_label.show_all()

        return GLib.SOURCE_REMOVE

    def _on_button_release(self, widget, event):
        # Закрываем дашборд только по правой кнопке и если есть notch
        if event.button == 3 and self.notch is not None:
            self.notch.close_notch()
            return True
        return False

    # -----------------------
    # Навигация по стеку
    # -----------------------

    def go_to_next_child(self):
        children = self.stack.get_children()
        if not children:
            return
        current_index = self.get_current_index(children)
        # если -1 (ничего не видно) — начинаем с 0
        if current_index == -1:
            self.stack.set_visible_child(children[0])
            return
        next_index = (current_index + 1) % len(children)
        self.stack.set_visible_child(children[next_index])

    def go_to_previous_child(self):
        children = self.stack.get_children()
        if not children:
            return
        current_index = self.get_current_index(children)
        if current_index == -1:
            self.stack.set_visible_child(children[-1])
            return
        previous_index = (current_index - 1) % len(children)
        self.stack.set_visible_child(children[previous_index])

    def get_current_index(self, children):
        current_child = self.stack.get_visible_child()
        try:
            return children.index(current_child)
        except ValueError:
            return -1

    def on_visible_child_changed(self, stack, param):
        visible = stack.get_visible_child()
        if visible is self.wallpapers:
            # Сброс поиска и фокус на поле
            self.wallpapers.search_entry.set_text("")
            self.wallpapers.search_entry.grab_focus()

    def go_to_section(self, section_name: str):
        """Навигация к конкретному разделу по имени ('widgets', 'pins', ...)."""
        widget = self._sections.get(section_name)
        if widget is not None:
            self.stack.set_visible_child(widget)
