"""Owned ctypes adapter for the public libgpiod 2.x C API, no framework native state."""

import ctypes as C
import sys


class DeviceError(OSError):
    pass


class Device:
    def __init__(self, config):
        if sys.platform != "linux":
            raise DeviceError("Linux required")
        self.config, self.chip, self.request = config, None, None
        self.lib = C.CDLL(config["library"], use_errno=True)
        pointer, uint, size, integer = C.c_void_p, C.c_uint, C.c_size_t, C.c_int
        signatures = {
            "chip_open": ([C.c_char_p], pointer), "chip_close": ([pointer], None),
            "line_settings_new": ([], pointer), "line_settings_free": ([pointer], None),
            "line_settings_set_direction": ([pointer, integer], integer),
            "line_settings_set_bias": ([pointer, integer], integer),
            "line_settings_set_active_low": ([pointer, C.c_bool], None),
            "line_settings_set_output_value": ([pointer, integer], integer),
            "line_config_new": ([], pointer), "line_config_free": ([pointer], None),
            "line_config_add_line_settings": ([pointer, C.POINTER(uint), size, pointer], integer),
            "request_config_new": ([], pointer), "request_config_free": ([pointer], None),
            "request_config_set_consumer": ([pointer, C.c_char_p], None),
            "chip_request_lines": ([pointer, pointer, pointer], pointer),
            "line_request_get_value": ([pointer, uint], integer),
            "line_request_set_value": ([pointer, uint, integer], integer),
            "line_request_release": ([pointer], None),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.lib, "gpiod_" + name)
            function.argtypes, function.restype = args, result
        self.chip = self.pointer(self.lib.gpiod_chip_open(config["chip"].encode()))
        owned = []
        try:
            lines = self.pointer(self.lib.gpiod_line_config_new())
            owned.append((self.lib.gpiod_line_config_free, lines))
            request = self.pointer(self.lib.gpiod_request_config_new())
            owned.append((self.lib.gpiod_request_config_free, request))
            self.lib.gpiod_request_config_set_consumer(request, b"bloxsmith-button-led")
            for name, direction in (("button", 2), ("led", 3)):
                offset = config[name + "_offset"]
                if offset < 0:
                    continue
                settings = self.pointer(self.lib.gpiod_line_settings_new())
                owned.append((self.lib.gpiod_line_settings_free, settings))
                self.checked(self.lib.gpiod_line_settings_set_direction(settings, direction))
                self.lib.gpiod_line_settings_set_active_low(settings, config[name + "_active_low"])
                if name == "button":
                    self.checked(self.lib.gpiod_line_settings_set_bias(settings, {"disabled": 3, "pull_up": 4, "pull_down": 5}[config["button_bias"]]))
                else:
                    self.checked(self.lib.gpiod_line_settings_set_output_value(settings, int(config["rest_active"])))
                offsets = (uint * 1)(offset)
                self.checked(self.lib.gpiod_line_config_add_line_settings(lines, offsets, 1, settings))
            self.request = self.pointer(self.lib.gpiod_chip_request_lines(self.chip, request, lines))
        except Exception:
            self.close(); raise
        finally:
            for release, value in reversed(owned):
                release(value)

    @staticmethod
    def checked(value):
        if value < 0:
            raise DeviceError(C.get_errno(), "GPIO operation failed")
        return value

    @staticmethod
    def pointer(value):
        if not value:
            raise DeviceError(C.get_errno(), "GPIO request failed")
        return value

    def read(self):
        value = self.checked(self.lib.gpiod_line_request_get_value(self.request, self.config["button_offset"]))
        if value not in (0, 1):
            raise DeviceError("Invalid GPIO value")
        return bool(value)

    def write(self, active):
        if self.config["led_offset"] >= 0:
            self.checked(self.lib.gpiod_line_request_set_value(self.request, self.config["led_offset"], int(active)))

    def close(self):
        try:
            if self.request:
                try:
                    self.write(self.config["rest_active"])
                finally:
                    self.lib.gpiod_line_request_release(self.request); self.request = None
        finally:
            if self.chip:
                self.lib.gpiod_chip_close(self.chip); self.chip = None
