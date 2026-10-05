"""Strict low-voltage button/indicator policy, independent of GPIO libraries."""

from collections.abc import Mapping
import json
from pathlib import Path
import re

DEFAULTS = {"enabled": False, "chip": "/dev/gpiochip0", "button_offset": -1,
    "led_offset": -1, "button_active_low": True, "led_active_low": False,
    "button_bias": "pull_up", "rest_active": False, "debounce_ms": 40,
    "poll_interval_ms": 10, "max_pattern_ms": 10000, "max_events_per_run": 10000,
    "library": "libgpiod.so.3"}


class GpioError(ValueError):
    pass


def fail(message):
    raise GpioError(message)


def integer(value, low, high, name):
    if type(value) is not int or not low <= value <= high:
        fail(f"{name} must be an integer between {low} and {high}.")
    return value


def configuration(raw):
    if not isinstance(raw, Mapping) or set(raw) - set(DEFAULTS) - {"execution", "runtime_path", "runtime_path_label"}:
        fail("Unknown GPIO configuration.")
    value = {**DEFAULTS, **{key: raw[key] for key in DEFAULTS if key in raw}}
    for name in ("enabled", "button_active_low", "led_active_low", "rest_active"):
        if type(value[name]) is not bool:
            fail(f"{name} requires a boolean.")
    if not isinstance(value["chip"], str) or not re.fullmatch(r"/dev/gpiochip[0-9]{1,5}", value["chip"]):
        fail("Choose an exact Linux /dev/gpiochipN device, not a header pin number.")
    for name in ("button_offset", "led_offset"):
        integer(value[name], -1, 65535, name)
    if value["button_offset"] >= 0 and value["button_offset"] == value["led_offset"]:
        fail("Button and LED must use distinct line offsets.")
    if value["enabled"] and max(value["button_offset"], value["led_offset"]) < 0:
        fail("Enable at least one button or LED offset before enabling hardware.")
    if value["button_bias"] not in ("disabled", "pull_up", "pull_down"):
        fail("Unsupported button bias.")
    for name, low, high in (("debounce_ms", 10, 1000), ("poll_interval_ms", 5, 100),
        ("max_pattern_ms", 100, 60000), ("max_events_per_run", 1, 100000)):
        integer(value[name], low, high, name)
    path = value["library"]
    if not isinstance(path, str) or len(path) > 4096 or any(ord(c) < 32 for c in path) or not (
        path == "libgpiod.so.3" or Path(path).is_absolute() and re.search(r"\.so(?:\.[0-9]+)*$", path)):
        fail("Use libgpiod.so.3 or an absolute path to a trusted libgpiod 2.x library.")
    return value


def document(raw):
    if isinstance(raw, Mapping):
        raw = json.dumps(dict(raw), allow_nan=False)
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 8192:
        fail("GPIO JSON exceeds 8 KiB.")
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                fail("Duplicate JSON field.")
            value[key] = item
        return value
    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: fail("Finite JSON required."))
    except (ValueError, RecursionError):
        fail("Expected one valid GPIO command object.")
    if not isinstance(value, dict):
        fail("Expected one GPIO command object.")
    return value


def encoded(value):
    raw = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode()
    if len(raw) > 8192:
        fail("GPIO message exceeds 8 KiB.")
    return raw


def command(raw, config):
    value = document(raw)
    action = value.get("action")
    allowed = {"status": set(), "pause": set(), "resume": set(), "off": set(),
        "on": {"duration_ms"}, "blink": {"on_ms", "off_ms", "count"}}
    if not isinstance(action, str) or action not in allowed or set(value) - allowed[action] - {"action", "request_id"}:
        fail("Supported GPIO commands: status, pause, resume, off, on or blink.")
    if "request_id" in value and (not isinstance(value["request_id"], str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value["request_id"])):
        fail("Invalid command correlation ID.")
    if action in {"on", "off", "blink"} and config["led_offset"] < 0:
        fail("No LED line is configured.")
    if action == "on":
        integer(value.get("duration_ms"), 1, config["max_pattern_ms"], "duration_ms")
    if action == "blink":
        for name in ("on_ms", "off_ms"):
            integer(value.get(name), 50, config["max_pattern_ms"], name)
        integer(value.get("count"), 1, 100, "count")
        if (value["on_ms"] + value["off_ms"]) * value["count"] > config["max_pattern_ms"]:
            fail("The complete blink pattern exceeds its configured duration limit.")
    return value


class Debouncer:
    """Require a stable sampled level; startup/resume establishes a baseline, not a press."""
    def __init__(self, delay):
        self.delay, self.candidate, self.since, self.stable = delay, None, 0, None

    def sample(self, value, now):
        if value != self.candidate:
            self.candidate, self.since = value, now
        if now - self.since >= self.delay and value != self.stable:
            previous, self.stable = self.stable, value
            return value if previous is not None else None
        return None
