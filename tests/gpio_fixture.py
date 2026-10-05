"""Compile a private synthetic native driver. No test touches installed GPIO hardware."""

import os
from pathlib import Path
import subprocess
import time


class Fixture:
    def __init__(self, root):
        self.path = Path(root) / "fixture-gpiod.so"
        subprocess.run(["gcc", "-shared", "-fPIC", "-O2", "-Wall", "-Werror", str(Path(__file__).with_name("gpiod_fixture.c")),
            "-ldl", "-o", str(self.path)], check=True, timeout=15, capture_output=True)
        self.set()

    def set(self, level=1, mode="ok"):
        temporary = self.path.with_suffix(".next")
        temporary.write_text(f"{level} {mode}\n", encoding="ascii")
        os.replace(temporary, str(self.path) + ".state")

    def config(self, **extra):
        return {"enabled": True, "chip": "/dev/gpiochip0", "button_offset": 17, "led_offset": 27,
            "library": str(self.path), "debounce_ms": 40, **extra}

    def calls(self):
        path = Path(str(self.path) + ".calls")
        return path.read_text().splitlines() if path.exists() else []


def until(listener, condition, seconds=6):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        listener.tick(time.monotonic())
        if condition():
            return
        time.sleep(.01)
    raise AssertionError("GPIO condition not observed")


def no_live_process(fixture):
    pids = {line.split()[0] for line in fixture.calls()}
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        alive = []
        for pid in pids:
            path = Path("/proc") / pid / "stat"
            try:
                if path.read_text().split(")", 1)[1].split()[0] not in ("Z", "X"):
                    alive.append(pid)
            except FileNotFoundError:
                pass
        if not alive:
            return
        time.sleep(.02)
    raise AssertionError("Owned GPIO process still alive: " + str(alive))
