import re
from typing import Optional, Dict, List, Type, Tuple
from abc import ABC, abstractmethod

from fabric.core.service import Property, Service, Signal
from fabric.utils import exec_shell_command_async, monitor_file
from gi.repository import GLib, Gio
from loguru import logger

import utils.functions as helpers


def glib_spawn_sync(command: str, timeout: int = 5) -> Tuple[bool, str, str]:
    """
    Выполнить команду синхронно через GLib.spawn_sync.
    Параметр timeout сейчас не используется (GLib.spawn_sync его не поддерживает).
    Возвращает (success, stdout, stderr).
    """
    try:
        success, stdout, stderr, status = GLib.spawn_sync(
            None,
            ["/bin/sh", "-c", command],
            None,
            GLib.SpawnFlags.DEFAULT,
            None,
            None,
        )
        stdout_str = stdout.decode("utf-8") if stdout else ""
        stderr_str = stderr.decode("utf-8") if stderr else ""
        # success флаг от GLib + проверка exit-кода
        return bool(success and status == 0), stdout_str, stderr_str
    except Exception as e:
        return False, "", str(e)


# Пытаемся подгрузить i2c-dev для ddcutil
try:
    success, stdout, stderr = glib_spawn_sync("modprobe i2c-dev", timeout=2)
    if success:
        logger.info("Loaded i2c-dev kernel module for ddcutil support")
    else:
        logger.warning(f"Failed to load i2c-dev module: {stderr}")
except Exception as e:
    logger.warning(f"Failed to load i2c-dev module: {e}")


class BrightnessBackend(ABC):
    """Базовый класс для любых бэкендов управления яркостью (0–100%)."""

    def __init__(self, name: str):
        self.name = name
        self.max_brightness = 100
        self.current_brightness = -1  # проценты 0–100 либо -1, если неизвестно
        self.available = False

    @abstractmethod
    def initialize(self) -> bool:
        """Инициализация бэкенда. Вернуть True, если всё ок."""
        pass

    @abstractmethod
    def get_brightness(self) -> int:
        """Текущая яркость (0–100). Вернуть -1, если недоступна."""
        pass

    @abstractmethod
    def set_brightness(self, percent: int) -> None:
        """Установить яркость в процентах (0–100)."""
        pass

    @abstractmethod
    def cleanup(self) -> None:
        """Освобождение ресурсов."""
        pass

    def is_available(self) -> bool:
        return self.available


class BrightnessCtlBackend(BrightnessBackend):
    """Яркость встроенного дисплея через brightnessctl + /sys/class/backlight."""

    def __init__(self):
        super().__init__("brightnessctl")
        self.device = ""
        self.backlight_path = ""
        self.monitor = None

    def initialize(self) -> bool:
        try:
            # Находим backlight-девайсы через Gio
            backlight_dir = Gio.File.new_for_path("/sys/class/backlight")
            try:
                enumerator = backlight_dir.enumerate_children(
                    "standard::*", Gio.FileQueryInfoFlags.NONE, None
                )
                devices = []
                info = enumerator.next_file(None)
                while info is not None:
                    devices.append(info.get_name())
                    info = enumerator.next_file(None)
                enumerator.close(None)
            except Exception:
                devices = []

            if not devices:
                logger.warning("No backlight devices found, brightness control disabled")
                return False

            if len(devices) > 1:
                logger.warning(
                    f"Multiple backlight devices found: {devices}. Using {devices[0]}"
                )

            self.device = devices[0]
            self.backlight_path = GLib.build_filename("/sys/class/backlight", self.device)

            # Максимальное сырое значение
            self.max_brightness = self._read_max_brightness()

            # Следим за файлом яркости для живого обновления
            brightness_file = GLib.build_filename(self.backlight_path, "brightness")
            self.monitor = monitor_file(brightness_file)
            self.monitor.connect(
                "changed",
                lambda _, file, *args: self._on_brightness_changed(file),
            )

            self.available = True
            logger.info(f"BrightnessCtl backend initialized for device: {self.device}")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize brightnessctl backend: {e}")
            return False

    def _read_max_brightness(self) -> int:
        """Сырое max_значение яркости из /sys."""
        max_brightness_path = GLib.build_filename(self.backlight_path, "max_brightness")
        max_file = Gio.File.new_for_path(max_brightness_path)
        if max_file.query_exists(None):
            try:
                success, contents, etag = max_file.load_contents(None)
                if success:
                    return int(contents.decode("utf-8").strip())
            except Exception as e:
                logger.error(f"Error reading max brightness file: {e}")
        return 100  # fallback

    def _on_brightness_changed(self, file) -> None:
        """Обработка изменений файла яркости (для синхронизации current_brightness)."""
        try:
            raw_value = int(file.load_bytes()[0].get_data().decode().strip())
            if self.max_brightness <= 0:
                return
            new_brightness = int((raw_value / self.max_brightness) * 100)

            if new_brightness != self.current_brightness:
                self.current_brightness = new_brightness
                logger.debug(
                    f"Brightness changed to {new_brightness}% via file monitor"
                )
        except Exception as e:
            logger.error(f"Error processing brightness change: {e}")

    def get_brightness(self) -> int:
        """Текущая яркость (0–100) по содержимому /sys."""
        if not self.available:
            return -1

        brightness_path = GLib.build_filename(self.backlight_path, "brightness")
        brightness_file = Gio.File.new_for_path(brightness_path)
        if brightness_file.query_exists(None):
            try:
                success, contents, etag = brightness_file.load_contents(None)
                if success:
                    raw = int(contents.decode("utf-8").strip())
                    if self.max_brightness <= 0:
                        return -1
                    self.current_brightness = int((raw / self.max_brightness) * 100)
                    return self.current_brightness
            except Exception as e:
                logger.error(f"Error reading brightness file: {e}")

        logger.warning(f"Brightness file does not exist: {brightness_path}")
        return -1

    def set_brightness(self, percent: int) -> None:
        """Установить яркость (0–100%) через brightnessctl."""
        if not self.available:
            return

        percent = max(0, min(percent, 100))

        try:
            # Доверяем вычисление сырого уровня самому brightnessctl
            exec_shell_command_async(
                f"brightnessctl --device '{self.device}' set {percent}%"
            )
            logger.debug(
                f"Requested screen brightness {percent}% via brightnessctl "
                f"on device {self.device}"
            )
        except Exception as e:
            logger.error(f"Error setting screen brightness: {e}")

    def cleanup(self) -> None:
        if self.monitor:
            self.monitor = None


class DdcUtilBackend(BrightnessBackend):
    """Яркость внешних мониторов через ddcutil (VCP 0x10)."""

    DDCUTIL_PARAMS = ("--noverify --disable-dynamic-sleep --sleep-multiplier=0.05 --skip-ddc-checks --disable-udf")
    CACHE_INTERVAL = 3
    FALLBACK_THRESHOLD = 3

    def __init__(self):
        super().__init__("ddcutil")
        self.bus: Optional[int] = None
        self._last_update_time = 0.0
        self._last_max_raw = 100
        self._error_count = 0
        self._cache_timer_id: Optional[int] = None

    def initialize(self) -> bool:
        try:
            self.bus = self._detect_bus()
            if self.bus is None or self.bus < 0:
                return False

            self.max_brightness = self._read_max_brightness() or 100
            self._last_max_raw = self.max_brightness
            self._cache_timer_id = GLib.timeout_add_seconds(
                self.CACHE_INTERVAL, self._update_cache
            )
            self.available = True
            logger.info(f"DdcUtil backend initialized on bus {self.bus}")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize ddcutil backend: {e}")
            return False

    def get_brightness(self) -> int:
        if not self.available or self.bus is None:
            return -1

        # Лёгкий кэш на 1 секунду
        current_time = GLib.get_monotonic_time() / 1_000_000.0
        if (
            current_time - self._last_update_time < 1.0
            and self.current_brightness != -1
        ):
            return self.current_brightness

        try:
            success, stdout, stderr = glib_spawn_sync(
                f"ddcutil --bus {self.bus} {self.DDCUTIL_PARAMS} getvcp 10"
            )
            if success and stdout:
                self._error_count = 0
                match = re.search(
                    r"current value\s*=\s*(\d+).*max value\s*=\s*(\d+)", stdout
                )
                if match:
                    current, max_val = int(match.group(1)), int(match.group(2))
                    self._last_max_raw = max_val
                    if max_val > 0:
                        self.current_brightness = round((current / max_val) * 100)
                        self._last_update_time = current_time
                        return self.current_brightness
            else:
                self._handle_error()
        except Exception as e:
            logger.error(f"ddcutil getvcp exception: {e}")
            self._handle_error()
        return self.current_brightness if self.current_brightness != -1 else -1

    def set_brightness(self, percent: int) -> None:
        if not self.available or self.bus is None:
            return

        percent = max(0, min(percent, 100))
        raw = int((percent / 100.0) * self._last_max_raw)

        exec_shell_command_async(
            f"ddcutil --bus {self.bus} {self.DDCUTIL_PARAMS} --terse setvcp 10 {raw}",
            lambda code, out, err: self._handle_error() if code else None,
        )

    def _detect_bus(self) -> int:
        try:
            success, stdout, stderr = glib_spawn_sync("ddcutil detect")
            if success and stdout:
                matches = re.findall(r"I2C bus:\s*/dev/i2c-(\d+)", stdout)
                if matches:
                    return int(matches[0])
            logger.error("ddcutil detect: no I2C bus found")
        except Exception as e:
            logger.error(f"Exception in _detect_bus: {e}")
        return -1

    def _read_max_brightness(self) -> Optional[int]:
        try:
            success, stdout, stderr = glib_spawn_sync(
                f"ddcutil --bus {self.bus} {self.DDCUTIL_PARAMS} getvcp 10"
            )
            if success and stdout:
                match = re.search(r"max value\s*=\s*(\d+)", stdout)
                if match:
                    return int(match.group(1))
        except Exception as e:
            logger.error(f"Error reading max brightness: {e}")
        return None

    def _update_cache(self) -> bool:
        _ = self.get_brightness()
        return True

    def _handle_error(self):
        self._error_count += 1
        if self._error_count >= self.FALLBACK_THRESHOLD:
            logger.error(
                f"ddcutil failed {self._error_count} times, marking as unavailable"
            )
            self.available = False
            if self._cache_timer_id:
                GLib.source_remove(self._cache_timer_id)
                self._cache_timer_id = None

    def cleanup(self) -> None:
        if self._cache_timer_id:
            GLib.source_remove(self._cache_timer_id)
            self._cache_timer_id = None


class Brightness(Service):
    """
    Сервис управления яркостью с несколькими бэкендами (встроенный экран, DDC и т.д.).
    """

    instance: Optional["Brightness"] = None

    BACKEND_CLASSES: Dict[str, Type[BrightnessBackend]] = {
        "brightnessctl": BrightnessCtlBackend,
        "ddcutil": DdcUtilBackend,
    }

    @staticmethod
    def get_initial() -> "Brightness":
        if Brightness.instance is None:
            Brightness.instance = Brightness()
        return Brightness.instance

    @Signal
    def screen(self, value: int) -> None:
        """Legacy‑сигнал: яркость экрана (0–100%)."""
        pass

    @Signal
    def brightness_changed(self, source: str, value: int) -> None:
        """Сигнал: изменилась яркость конкретного источника."""
        pass

    @Signal
    def backend_available(self, backend_name: str) -> None:
        """Сигнал: бэкенд стал доступен."""
        pass

    @Signal
    def backend_unavailable(self, backend_name: str) -> None:
        """Сигнал: бэкенд стал недоступен."""
        pass

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # Бэкенды по имени
        self.backends: Dict[str, BrightnessBackend] = {}
        self.active_backends: List[str] = []
        self.primary_backend: Optional[str] = None

        self._initialize_backends()
        self._select_primary_backend()

    def _initialize_backends(self) -> None:
        for name, backend_class in self.BACKEND_CLASSES.items():
            backend = backend_class()
            if backend.initialize():
                self.backends[name] = backend
                self.active_backends.append(name)
                self.emit("backend_available", name)
                logger.info(f"Backend '{name}' initialized successfully")
            else:
                logger.debug(f"Backend '{name}' not available")

    def _select_primary_backend(self) -> None:
        """Приоритет: встроенный дисплей (brightnessctl) → внешние (ddcutil)."""
        priority_order = ["brightnessctl", "ddcutil"]

        for backend_name in priority_order:
            if backend_name in self.active_backends:
                self.primary_backend = backend_name
                logger.info(f"Selected primary backend: {backend_name}")
                return

        self.primary_backend = None
        logger.warning("No brightness backends available")

    # --- Public API ---

    def get_available_backends(self) -> List[str]:
        return self.active_backends.copy()

    def get_primary_backend(self) -> Optional[str]:
        return self.primary_backend

    def set_primary_backend(self, backend_name: str) -> bool:
        if backend_name in self.active_backends:
            self.primary_backend = backend_name
            logger.info(f"Primary backend changed to: {backend_name}")
            return True
        logger.error(f"Backend '{backend_name}' is not available")
        return False

    def get_brightness(self, backend_name: Optional[str] = None) -> int:
        backend = backend_name or self.primary_backend
        if backend and backend in self.backends:
            brightness = self.backends[backend].get_brightness()
            if brightness != -1:
                self.emit("brightness_changed", backend, brightness)
            return brightness
        return -1

    def set_brightness(self, percent: int, backend_name: Optional[str] = None) -> bool:
        backend = backend_name or self.primary_backend
        if backend and backend in self.backends:
            percent = max(0, min(percent, 100))
            self.backends[backend].current_brightness = percent
            self.backends[backend].set_brightness(percent)
            self.emit("brightness_changed", backend, percent)
            self.emit("screen", percent)
            return True
        return False

    # --- Legacy API ---

    @property
    def screen_brightness(self) -> int:
        return self.get_brightness()

    @screen_brightness.setter
    def screen_brightness(self, percent: int) -> None:
        self.set_brightness(percent)

    @property
    def max_screen(self) -> int:
        if self.primary_backend and self.primary_backend in self.backends:
            return self.backends[self.primary_backend].max_brightness
        return 100

    # --- Cleanup ---

    def cleanup(self) -> None:
        for backend in self.backends.values():
            backend.cleanup()
        self.backends.clear()
        self.active_backends.clear()
        self.primary_backend = None
        logger.info("Brightness service cleaned up")
