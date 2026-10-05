"""Host-side supervision: public listener stays responsive while native GPIO runs elsewhere."""

import os
from pathlib import Path
import subprocess
import sys
import time
from . import logic


class Child:
    def __init__(self, config):
        self.process = subprocess.Popen([sys.executable, "-I", "-B", "-u", str(Path(__file__).with_name("worker.py")), str(os.getpid())],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, close_fds=True,
            env={key: value for key, value in os.environ.items() if key in {"PATH", "LANG", "LC_ALL"}})
        self.incoming, self.outgoing = b"", bytearray(logic.encoded(config) + b"\n")
        os.set_blocking(self.process.stdin.fileno(), False)
        os.set_blocking(self.process.stdout.fileno(), False)

    def send(self, item):
        raw = logic.encoded(item) + b"\n"
        if len(self.outgoing) + len(raw) > 32768:
            logic.fail("GPIO command queue is full.")
        self.outgoing.extend(raw)

    def poll(self):
        if self.outgoing:
            try:
                count = os.write(self.process.stdin.fileno(), self.outgoing)
                del self.outgoing[:count]
            except BlockingIOError:
                pass
        items = []
        for _ in range(4):
            try:
                raw = os.read(self.process.stdout.fileno(), 16384)
            except BlockingIOError:
                break
            if not raw:
                break
            self.incoming += raw
            if len(self.incoming) > 65536:
                logic.fail("GPIO event buffer is full.")
            while b"\n" in self.incoming:
                raw, self.incoming = self.incoming.split(b"\n", 1)
                items.append(logic.document(raw))
                if len(items) > 128:
                    logic.fail("GPIO event rate exceeded.")
        if not items and self.process.poll() is not None:
            logic.fail("GPIO process ended.")
        return items

    def close(self):
        # EOF requests rest/release even if the managed framework host is killed.
        self.process.stdin.close()
        try:
            self.process.wait(timeout=.7)
        except subprocess.TimeoutExpired:
            self.process.kill(); self.process.wait(timeout=.3)
        self.process.stdout.close()


class Listener:
    def __init__(self, config, emit):
        self.config, self.emit, self.child = config, emit, None
        self.state = "starting" if config["enabled"] else "disabled"
        self.activity, self.events = time.monotonic(), 0
        self.report(self.state)

    def report(self, code, **extra):
        self.emit({"status": {"code": code, "state": self.state, "events": self.events, **extra}})

    def stop_child(self):
        if self.child is not None:
            self.child.close(); self.child = None

    def command(self, item):
        action, ident = item["action"], item.get("request_id")
        extra = {"request_id": ident} if ident else {}
        if action == "pause":
            self.stop_child(); self.state = "paused"; self.report("paused", **extra)
        elif action == "resume":
            if self.config["enabled"] and self.state in {"paused", "device_error"} and self.events < self.config["max_events_per_run"]:
                self.state = "starting"
            self.report(self.state, **extra)
        elif self.state == "listening" and self.child is not None:
            self.child.send(item)
        else:
            self.report(self.state if action == "status" else "not_listening", **extra)

    def tick(self, now):
        if self.state not in {"starting", "listening"}:
            return
        try:
            if self.child is None:
                self.child = Child({**self.config, "max_events_per_run": self.config["max_events_per_run"] - self.events})
                self.activity = now
            for item in self.child.poll():
                self.activity = now
                kind = item.pop("type", None)
                if kind == "ready":
                    self.state = "listening"; self.report("listening", **item)
                elif kind == "event":
                    if item.get("event") not in {"pressed", "released"} or type(item.get("active")) is not bool or item.get("offset") != self.config["button_offset"] or item.get("chip") != self.config["chip"]:
                        logic.fail("Invalid GPIO event.")
                    self.events += 1; self.emit({"event": item})
                elif kind == "status":
                    code = item.pop("code", None)
                    if code not in {"applied", "listening", "pattern_complete", "event_limit", "duplicate", "request_id_conflict", "device_error"}:
                        logic.fail("Invalid GPIO status.")
                    if code in {"device_error", "event_limit"}:
                        self.state = code; self.stop_child()
                    self.report(code, **item)
                    if self.child is None:
                        return
                elif kind != "heartbeat":
                    logic.fail("Invalid GPIO message.")
            if now - self.activity > 5:
                logic.fail("GPIO driver deadline exceeded.")
        except (OSError, ValueError):
            self.stop_child(); self.state = "device_error"; self.report("device_error")

    def close(self):
        self.stop_child()
