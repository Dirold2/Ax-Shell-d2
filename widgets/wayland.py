import re
from collections.abc import Iterable
from enum import Enum
from typing import Literal, cast

import cairo
import gi
from gi.repository import Gdk, GObject, Gtk
from loguru import logger

from fabric.core.service import Property
from fabric.utils.helpers import extract_css_values, get_enum_member
from fabric.widgets.window import Window

gi.require_version("Gtk", "3.0")

try:
    gi.require_version("GtkLayerShell", "0.1")
    from gi.repository import GtkLayerShell
except ImportError:
    raise ImportError(
        "gtk-layer-shell is not available. Install it and make sure you are running under Wayland."
    )


class WaylandWindowExclusivity(Enum):
    NONE = 1
    NORMAL = 2
    AUTO = 3


class Layer(GObject.GEnum):
    BACKGROUND = 0
    BOTTOM = 1
    TOP = 2
    OVERLAY = 3
    ENTRY_NUMBER = 4


class KeyboardMode(GObject.GEnum):
    NONE = 0
    EXCLUSIVE = 1
    ON_DEMAND = 2
    ENTRY_NUMBER = 3


class Edge(GObject.GEnum):
    LEFT = 0
    RIGHT = 1
    TOP = 2
    BOTTOM = 3
    ENTRY_NUMBER = 4


class WaylandWindow(Window):
    # -------------------
    # Layer
    # -------------------
    @Property(
        int,
        flags="read-write",
        default_value=2,
    )
    def layer(self) -> int:  # type: ignore
        return self._layer.value if hasattr(self._layer, 'value') else self._layer  # type: ignore

    @layer.setter
    def layer(
        self,
        value: Literal["background", "bottom", "top", "overlay"] | Layer | int,
    ) -> None:
        if isinstance(value, int):
            self._layer = value
        elif isinstance(value, str):
            self._layer = get_enum_member(Layer, value, default=Layer.TOP).value
        else:
            self._layer = value.value
        GtkLayerShell.set_layer(self, self._layer)

    # -------------------
    # Monitor
    # -------------------
    @Property(int, "read-write")
    def monitor(self) -> int:
        monitor = GtkLayerShell.get_monitor(self)
        if not monitor:
            return -1
        monitor = cast(Gdk.Monitor, monitor)
        display = monitor.get_display() or Gdk.Display.get_default()
        if not display:
            return -1
        for i in range(display.get_n_monitors()):
            if display.get_monitor(i) is monitor:
                return i
        return -1

    @monitor.setter
    def monitor(self, monitor: int | Gdk.Monitor) -> None:
        if isinstance(monitor, int):
            display = Gdk.Display.get_default()
            if not display:
                logger.warning("No default display available for monitor selection")
                return
            monitor = display.get_monitor(monitor)
        if monitor is not None:
            GtkLayerShell.set_monitor(self, monitor)

    # -------------------
    # Exclusivity (exclusive zone)
    # -------------------
    @Property(WaylandWindowExclusivity, "read-write")
    def exclusivity(self) -> WaylandWindowExclusivity:
        return self._exclusivity

    @exclusivity.setter
    def exclusivity(
        self, value: Literal["none", "normal", "auto"] | WaylandWindowExclusivity
    ) -> None:
        value = get_enum_member(
            WaylandWindowExclusivity, value, default=WaylandWindowExclusivity.NONE
        )
        self._exclusivity = value
        match value:
            case WaylandWindowExclusivity.NORMAL:
                GtkLayerShell.set_exclusive_zone(self, True)
            case WaylandWindowExclusivity.AUTO:
                GtkLayerShell.auto_exclusive_zone_enable(self)
            case _:
                GtkLayerShell.set_exclusive_zone(self, False)

    # -------------------
    # Pass-through (input transparency)
    # -------------------
    @Property(bool, "read-write", default_value=False)
    def pass_through(self) -> bool:
        return self._pass_through

    @pass_through.setter
    def pass_through(self, pass_through: bool = False) -> None:
        self._pass_through = pass_through
        # Прозрачный клик: пустой Region, иначе сбрасываем
        region = cairo.Region() if pass_through else None
        self.input_shape_combine_region(region)

    # -------------------
    # Keyboard mode (единая реализация, без дубликатов)
    # -------------------
    @Property(
        int,
        "read-write",
        default_value=0,
    )
    def keyboard_mode(self) -> int:
        """
        Приводим gtk-layer-shell режим к нашему enum:
        - если включён get_keyboard_interactivity → EXCLUSIVE
        - иначе смотрим get_keyboard_mode.
        """
        mode = GtkLayerShell.get_keyboard_mode(self)
        # В новых версиях рекомендуют get_keyboard_mode, interactivity — deprecated,
        # но она всё ещё полезна для совместимости.[web:346][web:351]
        if GtkLayerShell.get_keyboard_interactivity(self):
            return 1  # KeyboardMode.EXCLUSIVE
        # mode уже GtkLayerShellKeyboardMode (0..ENTRY_NUMBER)
        if mode == GtkLayerShell.KeyboardMode.ON_DEMAND:
            return 2  # KeyboardMode.ON_DEMAND
        if mode == GtkLayerShell.KeyboardMode.EXCLUSIVE:
            return 1  # KeyboardMode.EXCLUSIVE
        return 0  # KeyboardMode.NONE

    @keyboard_mode.setter
    def keyboard_mode(
        self,
        value: Literal["none", "exclusive", "on-demand"] | KeyboardMode | int,
    ) -> None:
        if isinstance(value, int):
            self._keyboard_mode = value
        elif isinstance(value, str):
            self._keyboard_mode = get_enum_member(
                KeyboardMode,
                value,
                default=KeyboardMode.NONE,
            ).value
        else:
            self._keyboard_mode = value.value
        # Переводим наш enum в GtkLayerShellKeyboardMode.[web:346][web:349]
        if self._keyboard_mode == 1:  # KeyboardMode.EXCLUSIVE
            GtkLayerShell.set_keyboard_mode(
                self, GtkLayerShell.KeyboardMode.EXCLUSIVE
            )
        elif self._keyboard_mode == 2:  # KeyboardMode.ON_DEMAND
            GtkLayerShell.set_keyboard_mode(
                self, GtkLayerShell.KeyboardMode.ON_DEMAND
            )
        else:
            GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)

    # -------------------
    # Anchor
    # -------------------
    @Property(tuple[Edge, ...], "read-write")
    def anchor(self) -> tuple[Edge, ...]:
        return tuple(
            x
            for x in (Edge.TOP, Edge.RIGHT, Edge.BOTTOM, Edge.LEFT)
            if GtkLayerShell.get_anchor(self, x)
        )

    @anchor.setter
    def anchor(self, value: str | Iterable[Edge]) -> None:
        self._anchor = value
        if isinstance(value, (list, tuple)) and all(
            isinstance(edge, Edge) for edge in value
        ):
            # BUGFIX: раньше каждый edge всегда выставлялся в True.[web:346][web:353]
            for edge in (Edge.TOP, Edge.RIGHT, Edge.BOTTOM, Edge.LEFT):
                anchored = edge in value
                GtkLayerShell.set_anchor(self, edge, anchored)
        elif isinstance(value, str):
            for edge, anchored in WaylandWindow.extract_edges_from_string(
                value
            ).items():
                GtkLayerShell.set_anchor(self, edge, anchored)

    # -------------------
    # Margin
    # -------------------
    @Property(tuple[int, ...], flags="read-write")
    def margin(self) -> tuple[int, ...]:
        return tuple(
            GtkLayerShell.get_margin(self, x)
            for x in (Edge.TOP, Edge.RIGHT, Edge.BOTTOM, Edge.LEFT)
        )

    @margin.setter
    def margin(self, value: str | Iterable[int]) -> None:
        for edge, mrgv in WaylandWindow.extract_margin(value).items():
            GtkLayerShell.set_margin(self, edge, mrgv)

    # -------------------
    # Init
    # -------------------
    def __init__(
        self,
        layer: Literal["background", "bottom", "top", "overlay"] | Layer = Layer.TOP,
        anchor: str = "",
        margin: str | Iterable[int] = "0px 0px 0px 0px",
        exclusivity: Literal["auto", "normal", "none"]
        | WaylandWindowExclusivity = WaylandWindowExclusivity.NONE,
        keyboard_mode: Literal["none", "exclusive", "on-demand"]
        | KeyboardMode = KeyboardMode.NONE,
        pass_through: bool = False,
        monitor: int | Gdk.Monitor | None = None,
        title: str = "fabric",
        type: Literal["top-level", "popup"] | Gtk.WindowType = Gtk.WindowType.TOPLEVEL,
        child: Gtk.Widget | None = None,
        name: str | None = None,
        visible: bool = True,
        all_visible: bool = False,
        style: str | None = None,
        style_classes: Iterable[str] | str | None = None,
        tooltip_text: str | None = None,
        tooltip_markup: str | None = None,
        h_align: Literal["fill", "start", "end", "center", "baseline"]
        | Gtk.Align
        | None = None,
        v_align: Literal["fill", "start", "end", "center", "baseline"]
        | Gtk.Align
        | None = None,
        h_expand: bool = False,
        v_expand: bool = False,
        size: Iterable[int] | int | None = None,
        **kwargs,
    ):
        Window.__init__(
            self,
            title=title,
            type=type,
            child=child,
            name=name,
            visible=False,
            all_visible=False,
            style=style,
            style_classes=style_classes,
            tooltip_text=tooltip_text,
            tooltip_markup=tooltip_markup,
            h_align=h_align,
            v_align=v_align,
            h_expand=h_expand,
            v_expand=v_expand,
            size=size,
            **kwargs,
        )

        self._layer = Layer.ENTRY_NUMBER
        self._keyboard_mode = KeyboardMode.NONE
        self._anchor = anchor
        self._exclusivity = WaylandWindowExclusivity.NONE
        self._pass_through = pass_through

        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, title)

        self.connect(
            "notify::title",
            lambda *_: GtkLayerShell.set_namespace(self, self.get_title()),
        )

        if monitor is not None:
            self.monitor = monitor

        self.layer = layer
        self.anchor = anchor
        self.margin = margin
        self.keyboard_mode = keyboard_mode
        self.exclusivity = exclusivity
        self.pass_through = pass_through

        if all_visible:
            self.show_all()
        elif visible:
            self.show()

    # -------------------
    # Helpers
    # -------------------
    def steal_input(self) -> None:
        GtkLayerShell.set_keyboard_interactivity(self, True)

    def return_input(self) -> None:
        GtkLayerShell.set_keyboard_interactivity(self, False)

    def show(self) -> None:
        super().show()
        self.do_handle_post_show_request()

    def show_all(self) -> None:
        super().show_all()
        self.do_handle_post_show_request()

    def do_handle_post_show_request(self) -> None:
        if not self.get_children():
            logger.warning(
                "[WaylandWindow] showing an empty window is not recommended, "
                "some compositors might freak out."
            )
        # Переустанавливаем input-shape после show(), иначе композитор может его сбросить.[web:363]
        self.pass_through = self._pass_through

    # -------------------
    # Static parsing helpers
    # -------------------
    @staticmethod
    def extract_anchor_values(string: str) -> tuple[str, ...]:
        """
        Извлекает направления (top/right/bottom/left) из строки вида "tbr".
        """
        direction_map = {"l": "left", "t": "top", "r": "right", "b": "bottom"}
        pattern = re.compile(r"\b(left|right|top|bottom)\b", re.IGNORECASE)
        matches = pattern.findall(string)
        return tuple(
            {
                direction_map[match.lower()[0]]
                for match in matches
                if match.lower()[0] in direction_map
            }
        )

    @staticmethod
    def extract_edges_from_string(string: str) -> dict["Edge", bool]:
        anchor_values = WaylandWindow.extract_anchor_values(string.lower())
        return {
            Edge.TOP: "top" in anchor_values,
            Edge.RIGHT: "right" in anchor_values,
            Edge.BOTTOM: "bottom" in anchor_values,
            Edge.LEFT: "left" in anchor_values,
        }

    @staticmethod
    def extract_margin(input: str | Iterable[int]) -> dict["Edge", int]:
        if isinstance(input, str):
            margins = extract_css_values(input.lower())
        elif isinstance(input, (tuple, list)) and len(input) == 4:
            margins = tuple(input)
        else:
            margins = (0, 0, 0, 0)
        return {
            Edge.TOP: int(margins[0]),
            Edge.RIGHT: int(margins[1]),
            Edge.BOTTOM: int(margins[2]),
            Edge.LEFT: int(margins[3]),
        }
