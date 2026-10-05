# Button & LED

[![Button & LED](media/thumbnail.webp)](media/cover.png)

<!-- block-metadata:start -->
[![Block version: 0.1.0](https://img.shields.io/badge/block-0.1.0-blue)](model.json)
[![BloxSmith compatibility: 1.0.9](https://img.shields.io/badge/BloxSmith-1.0.9-brightgreen)](compatibility.json)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

Verified BloxSmith versions: **1.0.9** (bundled-block tests; see [test evidence](compatibility.json)).
<!-- block-metadata:end -->

Connect an ordinary **low-voltage pushbutton and indicator LED** to a BloxSmith
workflow through installed Linux GPIO hardware and **libgpiod 2.x**. A button emits
debounced JSON press/release events; commands drive a finite LED indication.
Hardware is **disabled by default**. One Shot is always a passive preview.

## Hardware scope

Use an installed `/dev/gpiochipN`, available line offsets, a compatible kernel
GPIO character-device driver, `libgpiod.so.3` and OS-user access to the device.
No driver installation, permissions changes, root escalation or device discovery
with automatic selection is performed. The block supports one chip and up to one
button line plus one LED line. Either offset can be `-1` to disable that function.
The offsets must differ. **Offsets are not physical connector pin numbers** and
are not a universal BCM numbering scheme. Check your board's documentation.

This is not for mains, power relays, actuators or safety-critical control. Use a
correctly sized LED current-limiting resistor and respect the board's electrical
ratings. A software polarity setting does not make a pin voltage-compatible.
Provide the appropriate external bias: Linux internal pull resistors are best
effort and may be unsupported without a detectable error.

Use a kernel driver for devices already supported by an appropriate subsystem;
this block does not implement software I²C, SPI, PWM or a universal device driver.

## Setup and ports

Configure the exact chip, unused button/LED offsets, active-low polarity and pull
resistor policy. Enable hardware only after checking the circuit. Apply saves the
draft; restart an already prepared Run to use changed settings. The modal and
inspector share English/French labels, grouped advanced settings and explicit
Apply/Cancel behavior.

| Port | Direction | Meaning |
| --- | --- | --- |
| `command` | Optional input | JSON LED command, pause, resume or status request. |
| `event` | Output | Debounced `pressed` / `released`, physical edge, logical level, chip offset, timestamp and event ID. |
| `status` | Output | Availability, command acknowledgment, pattern completion and recoverable failures. |

Active Runtime acquires configured lines when the framework prepares its listener,
without needing an input or Play. Acquisition sets the LED's configured resting
state atomically with its output request. One Shot never opens a chip or loads the
native GPIO library, even when hardware is enabled in the saved configuration.

The button uses stable-level **software sampling**, not hardware pulse counting.
After acquisition/resume, a stable baseline is established without generating an
artificial press. Holding a button does not repeat. A changed level must remain
stable for `debounce_ms`; short pulses and transitions during pauses/scheduling
delays can be missed. Event timestamps are application observations in UTC, not
hardware interrupt timestamps. This is ordinary human interaction, not hard
real-time measurement. The per-Run event cap releases the lines; pause/resume
does not reset that cap.

Example event (active-low button):

```json
{"event":"pressed","active":true,"edge":"falling","chip":"/dev/gpiochip0","offset":17,"sampled":true,"timestamp":"2026-01-01T12:00:00+00:00","event_id":"event-correlation"}
```

## Commands

```json
{"action":"on","duration_ms":1500,"request_id":"notice-1"}
```

```json
{"action":"blink","on_ms":200,"off_ms":300,"count":3,"request_id":"notice-2"}
```

```json
{"action":"off"}
```

Also supported: `{"action":"pause"}`, `{"action":"resume"}` and
`{"action":"status"}`. Commands cannot change the chip, offsets, driver or
electrical settings. Invalid commands are rejected without executing GPIO writes.
LED commands are refused when no LED is configured or the listener is paused.

Every `on` requires a duration; each complete blink sequence is limited by
`max_pattern_ms`, with at most 100 repetitions and at least 50 ms in each phase.
These are indicator patterns, not PWM. A newly accepted LED command **replaces**
the current pattern; patterns are not queued. Late phases are skipped rather than
replayed as a rapid burst. Timed patterns end at `rest_active`; `off` cancels a
pattern and holds logical off until another command or lifecycle transition.

An optional `request_id` is acknowledged. The device process remembers the last
128 IDs: exact repeats are ignored, and conflicting payloads with the same ID are
refused. This cache is not durable and resets when the device is reopened. Do not
use it as an exactly-once business transaction guarantee. Successful writes mean
the driver accepted a logical output value, not that a sensor measured LED light.

## Stop, errors and resting state

Pause and Stop request the configured LED resting state, then release all owned
lines. Normal driver errors take the same cleanup path. A new `resume` is required
after a device failure; there is no automatic actuation retry. A new Run is needed
after reaching the event cap.

The device lives in its own process. If the managed/linked framework host is
terminated abruptly, its pipe closes and the device guardian still attempts rest
and release. A watchdog bounds stuck native calls and terminates the guardian;
the kernel then closes descriptors. **A stuck driver, forced kill, OS crash or
power loss cannot guarantee a final software write.** After release, Linux no
longer promises the requested electrical state. The physical circuit and its
pull resistor determine the actual idle voltage. `rest_active` is a best-effort
pre-release setting, not a fail-safe or a persistent output after Stop.

Native messages and host queues are bounded. Backpressure or an unresponsive
driver ends the owned device process rather than retaining an unlimited queue.
Ordinary device failures appear on `status`; they do not intentionally fail the
whole Run. The configured absolute library-path option is for trusted
administrator-installed libgpiod 2.x builds: **it loads native code** and is not a
sandbox for third-party libraries.

## Tests and license

The package suites compile an original small libgpiod-ABI simulator using GCC
in temporary storage, then exercise the real ctypes adapter and device processes.
They cover configuration, polarity, debounce, duration limits, command replacement,
pause/Stop, blocked calls, framework modes/origins and responsive translated UI.
They never access a physical GPIO chip. This is software qualification against a
synthetic device, not electrical or board-specific certification. GCC is a test
prerequisite, not a block runtime dependency. The private framework harness is not
included in this repository.

Block and original fixture: Apache-2.0. libgpiod and kernel components remain
separately installed with their own licenses; no native binary is redistributed.

Primary references: [libgpiod public API](https://libgpiod.readthedocs.io/en/v2.3.1/core_api.html),
[line settings](https://libgpiod.readthedocs.io/en/v2.3.1/core_line_settings.html),
[Linux GPIO line lifecycle and bias limitations](https://docs.kernel.org/userspace-api/gpio/gpio-v2-get-line-ioctl.html).
