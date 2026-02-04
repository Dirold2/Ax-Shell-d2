from typing import Optional, Callable, Dict, Any, List, Tuple
from dataclasses import dataclass
from enum import Enum
import os
import subprocess

from fabric.utils.helpers import exec_shell_command_async, get_relative_path
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.label import Label
from gi.repository import Gdk, GLib
from loguru import logger

import config.data as data
import modules.icons as icons


# ==================== Константы ====================

# Пути к скриптам
SCREENSHOT_SCRIPT = get_relative_path("../scripts/screenshot.sh")
POMODORO_SCRIPT = get_relative_path("../scripts/pomodoro.sh")
OCR_SCRIPT = get_relative_path("../scripts/ocr.sh")
GAMEMODE_SCRIPT = get_relative_path("../scripts/gamemode.sh")
SCREENRECORD_SCRIPT = get_relative_path("../scripts/screenrecord.sh")
COLORPICKER_SCRIPT = get_relative_path("../scripts/hyprpicker.sh")
ON_SCREEN_KEYBOARD_SCRIPT = get_relative_path("../scripts/on_screen_keyboard.sh")

# Интервалы обновления (секунды)
RECORDER_CHECK_INTERVAL = 2
GAMEMODE_CHECK_INTERVAL = 2
POMODORO_CHECK_INTERVAL = 2
KEYBOARD_CHECK_INTERVAL = 2

# Кнопки мыши
MOUSE_LEFT = 1
MOUSE_MIDDLE = 2
MOUSE_RIGHT = 3

# Параметры кнопок
BUTTON_NAME = "toolbox-button"
BUTTON_LABEL_NAME = "button-label"
SEPARATOR_NAME = "tool-sep"


# ==================== Enums ====================

class ScreenshotMode(Enum):
    """Режимы скриншотов"""
    FULL = "p"
    REGION = "s"
    WINDOW = "w"


class ColorFormat(Enum):
    """Форматы цвета"""
    HEX = "-hex"
    RGB = "-rgb"
    HSV = "-hsv"


# ==================== Tooltips ====================

class Tooltips:
    """Централизованное хранение всех tooltips"""

    SCREENSHOT_REGION = """Region Screenshot

Left Click: Take a screenshot of a selected region.
Right Click: Take a mockup screenshot of a selected region."""

    SCREENSHOT_FULL = """Screenshot

Left Click: Take a fullscreen screenshot.
Right Click: Take a mockup fullscreen screenshot."""

    SCREENSHOT_WINDOW = """Window Screenshot

Left Click: Take a screenshot of the active window.
Right Click: Take a mockup screenshot of the active window."""

    SCREENSHOTS_FOLDER = "Screenshots Directory"

    SCREENRECORD = "Screen Recorder"

    RECORDINGS_FOLDER = "Recordings Directory"

    OCR = "OCR"

    COLORPICKER = """Color Picker

Mouse:
Left Click: HEX
Middle Click: HSV
Right Click: RGB

Keyboard:
Enter: HEX
Shift+Enter: RGB
Ctrl+Enter: HSV"""

    GAMEMODE = "Game Mode\nDisables effects and window animations for better performance."

    POMODORO = "Pomodoro Timer"

    EMOJI = "Emoji Picker"

    KEYBOARD = "On-Screen Keyboard\nToggle virtual keyboard"


# ==================== Button Configuration ====================

@dataclass
class ButtonConfig:
    """Конфигурация кнопки"""
    icon: str
    tooltip: str
    on_click: Optional[Callable] = None
    on_button_press: Optional[Callable] = None
    on_key_press: Optional[Callable] = None
    name: str = BUTTON_NAME
    label_name: str = BUTTON_LABEL_NAME
    can_focus: bool = False

    def needs_custom_handlers(self) -> bool:
        """Проверить нужны ли кастомные обработчики"""
        return self.on_button_press is not None or self.on_key_press is not None


# ==================== Command Executor ====================

class CommandExecutor:
    """Класс для выполнения команд"""

    @staticmethod
    def execute(command: str, close_menu: Optional[Callable] = None):
        """Выполнить команду и закрыть меню"""
        exec_shell_command_async(command)
        if close_menu:
            close_menu()

    @staticmethod
    def execute_background(command: str, close_menu: Optional[Callable] = None):
        """Выполнить команду в фоне"""
        CommandExecutor.execute(
            f"bash -c 'nohup {command} > /dev/null 2>&1 & disown'",
            close_menu
        )

    @staticmethod
    def execute_script(script_path: str, args: str = "", close_menu: Optional[Callable] = None):
        """Выполнить скрипт"""
        command = f"bash {script_path}"
        if args:
            command += f" {args}"
        CommandExecutor.execute(command, close_menu)


# ==================== Screenshot Handler ====================

class ScreenshotHandler:
    """Обработчик скриншотов"""

    def __init__(self, close_menu: Callable):
        self.close_menu = close_menu

    def take_screenshot(self, mode: ScreenshotMode, mockup: bool = False):
        """Сделать скриншот"""
        args = mode.value
        if mockup:
            args += " mockup"
        CommandExecutor.execute_script(SCREENSHOT_SCRIPT, args, self.close_menu)

    def create_button_press_handler(self, mode: ScreenshotMode):
        """Создать обработчик нажатия кнопки мыши"""
        def handler(button, event):
            if event.type == Gdk.EventType.BUTTON_PRESS:
                if event.button == MOUSE_LEFT:
                    self.take_screenshot(mode, mockup=False)
                    return True
                elif event.button == MOUSE_RIGHT:
                    self.take_screenshot(mode, mockup=True)
                    return True
            return False
        return handler

    def create_key_press_handler(self, mode: ScreenshotMode):
        """Создать обработчик нажатия клавиш"""
        def handler(widget, event):
            if event.keyval in {Gdk.KEY_Return, Gdk.KEY_KP_Enter}:
                modifiers = event.get_state()
                mockup = bool(modifiers & Gdk.ModifierType.SHIFT_MASK)
                self.take_screenshot(mode, mockup=mockup)
                return True
            return False
        return handler


# ==================== Color Picker Handler ====================

class ColorPickerHandler:
    """Обработчик выбора цвета"""

    def __init__(self, close_menu: Callable):
        self.close_menu = close_menu

    def pick_color(self, color_format: ColorFormat):
        """Выбрать цвет"""
        CommandExecutor.execute_script(
            COLORPICKER_SCRIPT,
            color_format.value,
            self.close_menu
        )

    def on_button_press(self, button, event):
        """Обработчик нажатия кнопки мыши"""
        if event.type == Gdk.EventType.BUTTON_PRESS:
            format_map = {
                MOUSE_LEFT: ColorFormat.HEX,
                MOUSE_MIDDLE: ColorFormat.HSV,
                MOUSE_RIGHT: ColorFormat.RGB,
            }
            color_format = format_map.get(event.button)
            if color_format:
                self.pick_color(color_format)
                return True
        return False

    def on_key_press(self, widget, event):
        """Обработчик нажатия клавиш"""
        if event.keyval in {Gdk.KEY_Return, Gdk.KEY_KP_Enter}:
            modifiers = event.get_state()

            # Определить формат по модификаторам
            if modifiers & Gdk.ModifierType.SHIFT_MASK:
                color_format = ColorFormat.RGB
            elif modifiers & Gdk.ModifierType.CONTROL_MASK:
                color_format = ColorFormat.HSV
            else:
                color_format = ColorFormat.HEX

            self.pick_color(color_format)
            return True
        return False


# ==================== Status Checker ====================

class StatusChecker:
    """Базовый класс для проверки статуса"""

    def __init__(self, check_command: str, interval: int):
        self.check_command = check_command
        self.interval = interval
        self.timer_id: Optional[int] = None

    def start(self):
        """Запустить периодическую проверку"""
        self.timer_id = GLib.timeout_add_seconds(self.interval, self._check)
        return self.timer_id

    def stop(self):
        """Остановить проверку"""
        if self.timer_id:
            GLib.source_remove(self.timer_id)
            self.timer_id = None

    def _check(self):
        """Выполнить проверку"""
        GLib.Thread.new(f"{self.__class__.__name__}-check", self._check_thread, None)
        return True

    def _check_thread(self, user_data):
        """Поток проверки статуса"""
        try:
            result = self._execute_check()
            status = self._parse_result(result)
        except Exception as e:
            logger.error(f"Error checking status: {e}")
            status = False

        GLib.idle_add(self._update_ui, status)

    def _execute_check(self) -> subprocess.CompletedProcess:
        """Выполнить команду проверки"""
        return subprocess.run(
            self.check_command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )

    def _parse_result(self, result: subprocess.CompletedProcess) -> bool:
        """Разобрать результат"""
        raise NotImplementedError

    def _update_ui(self, status: bool):
        """Обновить UI"""
        raise NotImplementedError


class ScreenRecordChecker(StatusChecker):
    """Проверка статуса записи экрана"""

    def __init__(self, button: Button):
        super().__init__("pgrep -f gpu-screen-recorder", RECORDER_CHECK_INTERVAL)
        self.button = button

    def _parse_result(self, result: subprocess.CompletedProcess) -> bool:
        return result.returncode == 0

    def _update_ui(self, running: bool):
        if running:
            self.button.get_child().set_markup(icons.stop)
            self.button.add_style_class("recording")
        else:
            self.button.get_child().set_markup(icons.screenrecord)
            self.button.remove_style_class("recording")
        return False


class GameModeChecker(StatusChecker):
    """Проверка статуса game mode"""

    def __init__(self, button: Button):
        super().__init__(f"bash {GAMEMODE_SCRIPT} check", GAMEMODE_CHECK_INTERVAL)
        self.button = button

    def _parse_result(self, result: subprocess.CompletedProcess) -> bool:
        return result.stdout == b't\n'

    def _update_ui(self, enabled: bool):
        if enabled:
            self.button.get_child().set_markup(icons.gamemode_off)
        else:
            self.button.get_child().set_markup(icons.gamemode)
        return False


class PomodoroChecker(StatusChecker):
    """Проверка статуса Pomodoro"""

    def __init__(self, button: Button):
        super().__init__("pgrep -f pomodoro.sh", POMODORO_CHECK_INTERVAL)
        self.button = button

    def _parse_result(self, result: subprocess.CompletedProcess) -> bool:
        return result.returncode == 0

    def _update_ui(self, running: bool):
        if running:
            self.button.get_child().set_markup(icons.timer_on)
            self.button.add_style_class("pomodoro")
        else:
            self.button.get_child().set_markup(icons.timer_off)
            self.button.remove_style_class("pomodoro")
        return False


class KeyboardChecker(StatusChecker):
    """Проверка статуса экранной клавиатуры"""

    def __init__(self, button: Button):
        super().__init__(f"bash {ON_SCREEN_KEYBOARD_SCRIPT} check", KEYBOARD_CHECK_INTERVAL)
        self.button = button

    def _parse_result(self, result: subprocess.CompletedProcess) -> bool:
        return result.stdout.strip() == b't'

    def _update_ui(self, running: bool):
        if running:
            # Используем getattr для безопасного доступа к иконке
            keyboard_on_icon = getattr(icons, 'keyboard_on', icons.keyboard)
            self.button.get_child().set_markup(keyboard_on_icon)
            self.button.add_style_class("keyboard-active")
        else:
            self.button.get_child().set_markup(icons.keyboard)
            self.button.remove_style_class("keyboard-active")
        return False


# ==================== Button Factory ====================

class ButtonFactory:
    """Фабрика для создания кнопок"""

    @staticmethod
    def create_button(config: ButtonConfig) -> Button:
        """Создать кнопку из конфигурации"""
        # Базовые параметры кнопки
        button_kwargs = {
            "name": config.name,
            "tooltip_markup": config.tooltip,
            "child": Label(name=config.label_name, markup=config.icon),
            "h_expand": False,
            "v_expand": False,
            "h_align": "center",
            "v_align": "center",
        }

        # Добавить on_clicked только если он не None
        if config.on_click is not None:
            button_kwargs["on_clicked"] = config.on_click

        button = Button(**button_kwargs)

        if config.can_focus:
            button.set_can_focus(True)

        if config.on_button_press:
            button.connect("button-press-event", config.on_button_press)

        if config.on_key_press:
            button.connect("key-press-event", config.on_key_press)

        return button

    @staticmethod
    def create_separator() -> Box:
        """Создать разделитель"""
        return Box(
            name=SEPARATOR_NAME,
            h_expand=False,
            v_expand=False,
            h_align="center",
            v_align="center"
        )


# ==================== Directory Helper ====================

class DirectoryHelper:
    """Помощник для работы с директориями"""

    @staticmethod
    def get_screenshots_dir() -> str:
        """Получить директорию скриншотов"""
        pictures_dir = os.environ.get('XDG_PICTURES_DIR', os.path.expanduser('~/Pictures'))
        return os.path.join(pictures_dir, 'Screenshots')

    @staticmethod
    def get_recordings_dir() -> str:
        """Получить директорию записей"""
        videos_dir = os.environ.get('XDG_VIDEOS_DIR', os.path.expanduser('~/Videos'))
        return os.path.join(videos_dir, 'Recordings')

    @staticmethod
    def ensure_directory_exists(directory: str):
        """Убедиться что директория существует"""
        os.makedirs(directory, exist_ok=True)

    @staticmethod
    def open_directory(directory: str):
        """Открыть директорию"""
        DirectoryHelper.ensure_directory_exists(directory)
        exec_shell_command_async(f"xdg-open {directory}")


# ==================== Toolbox ====================

class Toolbox(Box):
    """Панель инструментов с различными утилитами"""

    def __init__(self, notch, **kwargs):
        """
        Инициализация Toolbox

        Args:
            notch: Ссылка на notch для управления меню
            **kwargs: Дополнительные параметры для Box
        """
        # Определить ориентацию
        orientation = self._calculate_orientation()

        super().__init__(
            name="toolbox",
            orientation=orientation,
            spacing=4,
            v_align="center",
            h_align="center",
            visible=True,
            **kwargs,
        )

        self.notch = notch

        # Инициализация обработчиков
        self._init_handlers()

        # Создание кнопок
        self._create_buttons()

        # Запуск проверок статуса
        self._start_status_checkers()

        self.show_all()

    def _calculate_orientation(self) -> str:
        """Вычислить ориентацию панели"""
        is_vertical = (
            data.PANEL_THEME == "Panel" and 
            (data.BAR_POSITION in ["Left", "Right"] or 
             data.PANEL_POSITION in ["Start", "End"])
        )
        return "v" if is_vertical else "h"

    def _init_handlers(self):
        """Инициализировать обработчики"""
        self.screenshot_handler = ScreenshotHandler(self.close_menu)
        self.colorpicker_handler = ColorPickerHandler(self.close_menu)

    def _create_buttons(self):
        """Создать все кнопки"""
        # Создание кнопок для скриншотов
        self.btn_ssregion = self._create_screenshot_button(
            ScreenshotMode.REGION,
            icons.ssregion,
            Tooltips.SCREENSHOT_REGION
        )

        self.btn_sswindow = self._create_screenshot_button(
            ScreenshotMode.WINDOW,
            icons.sswindow,
            Tooltips.SCREENSHOT_WINDOW
        )

        self.btn_ssfull = self._create_screenshot_button(
            ScreenshotMode.FULL,
            icons.ssfull,
            Tooltips.SCREENSHOT_FULL
        )

        # Кнопка папки скриншотов
        self.btn_screenshots_folder = ButtonFactory.create_button(ButtonConfig(
            icon=icons.screenshots,
            tooltip=Tooltips.SCREENSHOTS_FOLDER,
            on_click=self._open_screenshots_folder
        ))

        # Кнопка записи экрана
        self.btn_screenrecord = ButtonFactory.create_button(ButtonConfig(
            icon=icons.screenrecord,
            tooltip=Tooltips.SCREENRECORD,
            on_click=self._screenrecord
        ))

        # Кнопка папки записей
        self.btn_recordings_folder = ButtonFactory.create_button(ButtonConfig(
            icon=icons.recordings,
            tooltip=Tooltips.RECORDINGS_FOLDER,
            on_click=self._open_recordings_folder
        ))

        # Кнопка OCR
        self.btn_ocr = ButtonFactory.create_button(ButtonConfig(
            icon=icons.ocr,
            tooltip=Tooltips.OCR,
            on_click=self._ocr
        ))

        # Кнопка выбора цвета
        self.btn_color = ButtonFactory.create_button(ButtonConfig(
            icon=icons.colorpicker,
            tooltip=Tooltips.COLORPICKER,
            on_button_press=self.colorpicker_handler.on_button_press,
            on_key_press=self.colorpicker_handler.on_key_press,
            can_focus=True
        ))

        # Кнопка game mode
        self.btn_gamemode = ButtonFactory.create_button(ButtonConfig(
            icon=icons.gamemode,
            tooltip=Tooltips.GAMEMODE,
            on_click=self._gamemode
        ))

        # Кнопка Pomodoro
        self.btn_pomodoro = ButtonFactory.create_button(ButtonConfig(
            icon=icons.timer_off,
            tooltip=Tooltips.POMODORO,
            on_click=self._pomodoro
        ))

        # Кнопка emoji
        self.btn_emoji = ButtonFactory.create_button(ButtonConfig(
            icon=icons.emoji,
            tooltip=Tooltips.EMOJI,
            on_click=self._emoji
        ))

        # Кнопка экранной клавиатуры
        keyboard_icon = getattr(icons, 'keyboard', '⌨️')
        self.btn_keyboard = ButtonFactory.create_button(ButtonConfig(
            icon=keyboard_icon,
            tooltip=Tooltips.KEYBOARD,
            on_click=self._keyboard
        ))

        # Собрать все кнопки в порядке отображения
        self.buttons = self._arrange_buttons()

        # Добавить кнопки в контейнер
        for button in self.buttons:
            self.add(button)

    def _create_screenshot_button(
        self,
        mode: ScreenshotMode,
        icon: str,
        tooltip: str
    ) -> Button:
        """Создать кнопку скриншота"""
        return ButtonFactory.create_button(ButtonConfig(
            icon=icon,
            tooltip=tooltip,
            on_button_press=self.screenshot_handler.create_button_press_handler(mode),
            on_key_press=self.screenshot_handler.create_key_press_handler(mode),
            can_focus=True
        ))

    def _arrange_buttons(self) -> List:
        """Расположить кнопки в правильном порядке"""
        return [
            # Группа скриншотов
            self.btn_ssregion,
            self.btn_sswindow,
            self.btn_ssfull,
            self.btn_screenshots_folder,

            ButtonFactory.create_separator(),

            # Группа записи
            self.btn_screenrecord,
            self.btn_recordings_folder,

            ButtonFactory.create_separator(),

            # Группа утилит
            self.btn_ocr,
            self.btn_color,

            ButtonFactory.create_separator(),

            # Группа режимов
            self.btn_gamemode,
            self.btn_pomodoro,
            self.btn_emoji,
            self.btn_keyboard,
        ]

    def _start_status_checkers(self):
        """Запустить проверки статуса"""
        self.recorder_checker = ScreenRecordChecker(self.btn_screenrecord)
        self.recorder_checker.start()

        self.gamemode_checker = GameModeChecker(self.btn_gamemode)
        self.gamemode_checker.start()

        self.pomodoro_checker = PomodoroChecker(self.btn_pomodoro)
        self.pomodoro_checker.start()

        self.keyboard_checker = KeyboardChecker(self.btn_keyboard)
        self.keyboard_checker.start()

    # ==================== Menu Control ====================

    def close_menu(self):
        """Закрыть меню"""
        self.notch.close_notch()

    # ==================== Screenshot Actions ====================

    def ssregion(self, *args):
        """Сделать скриншот региона"""
        self.screenshot_handler.take_screenshot(ScreenshotMode.REGION)

    def sswindow(self, *args):
        """Сделать скриншот окна"""
        self.screenshot_handler.take_screenshot(ScreenshotMode.WINDOW)

    def ssfull(self, *args, mockup=False):
        """Сделать полный скриншот"""
        self.screenshot_handler.take_screenshot(ScreenshotMode.FULL, mockup=mockup)

    # ==================== Other Actions ====================

    def _screenrecord(self, *args):
        """Запустить/остановить запись экрана"""
        CommandExecutor.execute_background(
            f"bash {SCREENRECORD_SCRIPT}",
            self.close_menu
        )

    def _ocr(self, *args):
        """Запустить OCR"""
        CommandExecutor.execute_script(OCR_SCRIPT, "s", self.close_menu)

    def _gamemode(self, *args):
        """Переключить game mode"""
        CommandExecutor.execute_script(GAMEMODE_SCRIPT, "", None)
        self.gamemode_checker._check()
        self.close_menu()

    def _pomodoro(self, *args):
        """Запустить Pomodoro таймер"""
        CommandExecutor.execute_background(
            f"bash {POMODORO_SCRIPT}",
            self.close_menu
        )

    def _emoji(self, *args):
        """Открыть emoji picker"""
        self.notch.open_notch("emoji")

    def _keyboard(self, *args):
        """Переключить экранную клавиатуру"""
        CommandExecutor.execute_script(ON_SCREEN_KEYBOARD_SCRIPT, "toggle", None)
        # Немедленная проверка статуса (с задержкой для запуска клавиатуры)
        GLib.timeout_add(500, self.keyboard_checker._check)
        self.close_menu()

    def _open_screenshots_folder(self, *args):
        """Открыть папку со скриншотами"""
        DirectoryHelper.open_directory(DirectoryHelper.get_screenshots_dir())
        self.close_menu()

    def _open_recordings_folder(self, *args):
        """Открыть папку с записями"""
        DirectoryHelper.open_directory(DirectoryHelper.get_recordings_dir())
        self.close_menu()

    # ==================== Cleanup ====================

    def destroy(self):
        """Очистить ресурсы"""
        if hasattr(self, 'recorder_checker'):
            self.recorder_checker.stop()
        if hasattr(self, 'gamemode_checker'):
            self.gamemode_checker.stop()
        if hasattr(self, 'pomodoro_checker'):
            self.pomodoro_checker.stop()
        if hasattr(self, 'keyboard_checker'):
            self.keyboard_checker.stop()
        super().destroy()