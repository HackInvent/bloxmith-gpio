"""Single owned GPIO guardian: rest/release on EOF, bounded calls and no shell."""

from collections import OrderedDict
from datetime import datetime, timezone
import os
from pathlib import Path
import signal
import sys
import threading
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from device import Device
from logic import GpioError, configuration, command, document, encoded, Debouncer


def emit(value):
    raw = encoded(value) + b"\n"
    if os.write(sys.stdout.fileno(), raw) != len(raw):
        raise GpioError("Output backpressure")


def main():
    if len(sys.argv) != 2 or sys.platform != "linux":
        raise SystemExit(2)
    parent = int(sys.argv[1])
    if os.getppid() != parent:
        raise SystemExit(2)
    config = configuration(document(sys.stdin.buffer.readline(8193)))
    if not config["enabled"]:
        raise SystemExit(2)
    os.set_blocking(sys.stdin.fileno(), False)
    os.set_blocking(sys.stdout.fileno(), False)
    stopping, device, deadline = False, None, time.monotonic() + 4
    def stop(*_):
        nonlocal stopping
        stopping = True
    def watchdog():
        # ctypes releases the GIL. A stuck trusted driver must not orphan this
        # process after its host dies. Forced exit releases descriptors, but it
        # cannot promise a voltage: external rest bias is required.
        while True:
            if time.monotonic() >= deadline:
                os._exit(75)
            time.sleep(.05)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    threading.Thread(target=watchdog, daemon=True).start()
    pending, seen, count = b"", OrderedDict(), 0
    pattern, last_heartbeat = None, 0
    last_value = config["rest_active"]
    debouncer = Debouncer(config["debounce_ms"] / 1000)
    def write(value):
        nonlocal last_value
        if value != last_value:
            device.write(value); last_value = value
    try:
        device = Device(config)
        emit({"type": "ready", "led_active": last_value})
        while not stopping and os.getppid() == parent:
            now = time.monotonic()
            deadline = now + 4
            try:
                incoming = os.read(sys.stdin.fileno(), 8192)
            except BlockingIOError:
                incoming = None
            if incoming == b"":
                break
            if incoming:
                pending += incoming
                if len(pending) > 32768:
                    raise GpioError("Command overflow")
                for _ in range(32):
                    if b"\n" not in pending:
                        break
                    raw, pending = pending.split(b"\n", 1)
                    item = command(raw, config)
                    action, ident = item["action"], item.get("request_id")
                    signature = encoded(item)
                    if ident and ident in seen:
                        emit({"type": "status", "code": "duplicate" if seen[ident] == signature else "request_id_conflict", "request_id": ident})
                        continue
                    if ident:
                        seen[ident] = signature
                        if len(seen) > 128:
                            seen.popitem(last=False)
                    if action == "on":
                        pattern = (now, item["duration_ms"] / 1000, 0, 1)
                        write(True)
                    elif action == "off":
                        pattern = None; write(False)
                    elif action == "blink":
                        pattern = (now, item["on_ms"] / 1000, item["off_ms"] / 1000, item["count"])
                        write(True)
                    elif action != "status":
                        raise GpioError("Unknown device command")
                    emit({"type": "status", "code": "applied" if action != "status" else "listening",
                        "led_active": last_value, **({"request_id": ident} if ident else {})})
            if pattern:
                start, on, off, repeat = pattern
                elapsed, period = now - start, on + off
                if elapsed >= period * repeat:
                    pattern = None; write(config["rest_active"])
                    emit({"type": "status", "code": "pattern_complete", "led_active": last_value})
                else:
                    # Skip missed phases: never burst stale toggles after scheduling delays.
                    write(elapsed % period < on)
            if config["button_offset"] >= 0:
                change = debouncer.sample(device.read(), now)
                if change is not None:
                    count += 1
                    emit({"type": "event", "event": "pressed" if change else "released", "active": change,
                        "edge": "rising" if change != config["button_active_low"] else "falling",
                        "event_id": uuid.uuid4().hex, "chip": config["chip"], "offset": config["button_offset"],
                        "timestamp": datetime.now(timezone.utc).isoformat(), "sampled": True})
                    if count >= config["max_events_per_run"]:
                        emit({"type": "status", "code": "event_limit"}); break
            if now - last_heartbeat >= .5:
                emit({"type": "heartbeat"}); last_heartbeat = now
            time.sleep(config["poll_interval_ms"] / 1000)
    except (OSError, ValueError, AttributeError) as exc:
        try:
            reason = {2: "device_or_library_missing", 13: "permission_denied", 16: "lines_busy",
                22: "unsupported_configuration", 95: "unsupported_driver"}.get(getattr(exc, "errno", None), "device_io")
            if isinstance(exc, AttributeError):
                reason = "libgpiod_2_required"
            emit({"type": "status", "code": "device_error", "reason": reason})
        except (OSError, ValueError):
            pass
    finally:
        if device is not None:
            device.close()


if __name__ == "__main__":
    main()
