"""FB1/FB2/FB3/FB4: strict policy, actual ctypes native ABI, debounce, LED and process cleanup."""

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "tests"), str(Path(__file__).parent)]
from blocs.gpio import logic, engine
from gpio_fixture import Fixture, until, no_live_process


def refused(action):
    try:
        action()
    except (ValueError, TypeError):
        return
    raise AssertionError("Invalid GPIO policy accepted")


def main():
    for bad in ({"enabled": "true"}, {"enabled": True}, {"chip": "/dev/mem"}, {"button_offset": True},
        {"button_offset": 1, "led_offset": 1}, {"debounce_ms": 0}, {"poll_interval_ms": 1}, {"library": "other.so"}):
        refused(lambda: logic.configuration(bad))
    config = logic.configuration({"led_offset": 2})
    for bad in ({"action": "on"}, {"action": "on", "duration_ms": 10001}, {"action": "write", "offset": 5},
        {"action": "blink", "on_ms": 5, "off_ms": 500, "count": 1}, {"action": "blink", "on_ms": 100, "off_ms": 100, "count": 100},
        '{"action":"off","action":"on"}', {"action": "off", "request_id": "\n"}):
        refused(lambda: logic.command(bad, config))
    debounce = logic.Debouncer(.04)
    assert debounce.sample(False, 0) is None and debounce.sample(False, .1) is None
    assert debounce.sample(True, .11) is None and debounce.sample(False, .12) is None
    assert debounce.sample(True, .13) is None and debounce.sample(True, .18) is True
    assert debounce.sample(True, 5) is None
    disabled = engine.Listener(logic.configuration({}), lambda _: None)
    disabled.command({"action": "resume"}); disabled.tick(time.monotonic())
    assert disabled.state == "disabled" and disabled.child is None
    with TemporaryDirectory(prefix="gpio-native-") as temporary:
        fixture = Fixture(Path(temporary)); results = []
        cfg = logic.configuration(fixture.config())
        listener = engine.Listener(cfg, results.append)
        def code(name):
            return any(item.get("status", {}).get("code") == name for item in results)
        def events():
            return [item["event"] for item in results if "event" in item]
        def send(item):
            listener.command(logic.command(item, cfg))
        try:
            until(listener, lambda: listener.state == "listening")
            until(listener, lambda: time.monotonic() - listener.activity > .1)
            assert not events()
            fixture.set(level=0)
            until(listener, lambda: len(events()) == 1)
            assert events()[0]["event"] == "pressed" and events()[0]["edge"] == "falling"
            fixture.set(level=1)
            until(listener, lambda: len(events()) == 2)
            assert events()[-1]["event"] == "released"
            send({"action": "on", "duration_ms": 200, "request_id": "one"})
            until(listener, lambda: any("WRITE 27 1" in line for line in fixture.calls()))
            send({"action": "on", "duration_ms": 200, "request_id": "one"})
            until(listener, lambda: code("duplicate"))
            send({"action": "on", "duration_ms": 1000, "request_id": "one"})
            until(listener, lambda: code("request_id_conflict"))
            until(listener, lambda: code("pattern_complete"))
            assert "WRITE 27 0" in fixture.calls()[-1]
            # Replacement cancels the long pattern immediately, not at its end.
            before = len(fixture.calls())
            send({"action": "on", "duration_ms": 5000, "request_id": "replace-on"})
            until(listener, lambda: any("WRITE 27 1" in call for call in fixture.calls()[before:]))
            send({"action": "off", "request_id": "replace-off"})
            until(listener, lambda: any("WRITE 27 0" in call for call in fixture.calls()[before:]))
            before = sum("WRITE 27 1" in line for line in fixture.calls())
            send({"action": "blink", "on_ms": 60, "off_ms": 60, "count": 2, "request_id": "blink"})
            until(listener, lambda: sum("WRITE 27 1" in line for line in fixture.calls()) >= before + 2)
            send({"action": "pause"})
            assert listener.child is None and "RELEASE" in fixture.calls()[-2]
            assert "WRITE 27 0" in fixture.calls()[-3]
            send({"action": "resume"}); until(listener, lambda: listener.state == "listening")
            fixture.set(mode="hang")
            until(listener, lambda: listener.state == "device_error")
            no_live_process(fixture)
        finally:
            listener.close()
        assert not any("BAD_" in line for line in fixture.calls())
        fixture.set(mode="busy"); failed = []
        listener = engine.Listener(cfg, failed.append)
        try:
            until(listener, lambda: listener.state == "device_error")
            assert any(item.get("status", {}).get("reason") == "lines_busy" for item in failed)
        finally:
            listener.close()
        no_live_process(fixture)
        fixture.set(); results = []
        cfg = logic.configuration(fixture.config(led_active_low=True, rest_active=True, max_events_per_run=1))
        start = len(fixture.calls())
        listener = engine.Listener(cfg, results.append)
        try:
            until(listener, lambda: listener.state == "listening")
            until(listener, lambda: time.monotonic() - listener.activity > .1)
            assert any("INITIAL 27 0" in line for line in fixture.calls()[start:])
            listener.command({"action": "off"})
            until(listener, lambda: any("WRITE 27 1" in line for line in fixture.calls()[start:]))
            fixture.set(level=0)
            until(listener, lambda: listener.state == "event_limit")
            assert listener.events == 1 and listener.child is None
            assert "WRITE 27 0" in fixture.calls()[-3], fixture.calls()
            listener.command({"action": "resume"}); listener.tick(time.monotonic())
            assert listener.state == "event_limit" and listener.child is None
        finally:
            listener.close()
        no_live_process(fixture)
    print("[ok] GPIO native ABI, polarity, debounced press/release, bounded LED, dedup, pause/Stop and stuck-call watchdog")


if __name__ == "__main__":
    main()
