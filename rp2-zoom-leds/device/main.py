import sys
import time

try:
    import uselect as select
except ImportError:
    import select

import config
from led_strip import LedStrip, StatusLed
from serial_protocol import normalize_command, try_parse_line
from state_mapper import command_from_state

try:
    from state_client import fetch_state
    from wifi_connect import connect_wifi
except ImportError:
    fetch_state = None
    connect_wifi = None

try:
    from ota_client import check_for_update
except Exception:
    check_for_update = None

try:
    from telemetry import from_config as telemetry_from_config
except Exception:
    telemetry_from_config = None


class UsbCommandReader:
    def __init__(self):
        self.buffer = ""
        self.poller = select.poll()
        self.poller.register(sys.stdin, select.POLLIN)

    def poll(self):
        command = None

        while self.poller.poll(0):
            char = sys.stdin.read(1)
            if not char:
                break
            if isinstance(char, bytes):
                char = char.decode("utf-8")

            if char == "\n":
                line = self.buffer.strip()
                self.buffer = ""
                if line:
                    command = try_parse_line(line)
            elif char != "\r":
                if len(self.buffer) < 512:
                    self.buffer += char
                else:
                    self.buffer = ""

        return command


def main():
    strip = LedStrip(config.LED_PIN, config.LED_COUNT, config.BRIGHTNESS)
    status = StatusLed()
    reader = UsbCommandReader()
    telemetry = telemetry_from_config(config) if telemetry_from_config else NullTelemetry()
    telemetry.log(
        "boot",
        network_enabled=bool(getattr(config, "NETWORK_ENABLED", False)),
        state_poll_seconds=int(getattr(config, "STATE_POLL_SECONDS", 0)),
        loop_delay_ms=int(getattr(config, "LOOP_DELAY_MS", 0)),
    )
    network = NetworkCommandReader(telemetry)

    run_startup_self_test(strip, status, telemetry)

    while True:
        command = reader.poll()
        if command is not None:
            apply_command(strip, status, command, telemetry, "usb")
            network.pause_for_serial_override()
        else:
            command = network.poll()
            if command is not None:
                apply_command(strip, status, command, telemetry, "network")

        strip.tick()

        if not strip.available:
            status.tick_heartbeat(config.STATUS_BLINK_MS)

        time.sleep_ms(config.LOOP_DELAY_MS)


def run_startup_self_test(strip, status, telemetry):
    if not getattr(config, "STARTUP_SELF_TEST", False):
        return

    step_ms = int(getattr(config, "STARTUP_SELF_TEST_STEP_MS", 2000))
    commands = getattr(config, "STARTUP_SELF_TEST_COMMANDS", ())

    telemetry.log("self_test_start", steps=len(commands), step_ms=step_ms)
    for command in commands:
        ok = apply_command(strip, status, command, telemetry, "self_test")
        if ok:
            status.set(0)

        started = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), started) < step_ms:
            strip.tick()
            if not strip.available:
                status.tick_heartbeat(config.STATUS_BLINK_MS)
            time.sleep_ms(config.LOOP_DELAY_MS)
    telemetry.log("self_test_done")


def apply_command(strip, status, command, telemetry, source):
    started = time.ticks_ms()
    try:
        normalized = normalize_command(command)
    except Exception as exc:
        print("Ignoring invalid command:", exc)
        telemetry.log("command_invalid", source=source, error=str(exc))
        return False

    ok = strip.apply(normalized)
    if ok:
        status.set(0)
    telemetry.log(
        "command_apply",
        source=source,
        ok=ok,
        elapsed_ms=time.ticks_diff(time.ticks_ms(), started),
        command=normalized,
    )
    return ok


def state_summary(state):
    if not isinstance(state, dict):
        return {}

    summary = {}
    for key in (
        "meeting_status",
        "in_use",
        "minutes",
        "minutes_until_next",
        "minutes_until_end",
        "color",
        "label",
    ):
        if key in state:
            summary[key] = state.get(key)
    if "next_meeting_id" in state:
        summary["has_next_meeting"] = bool(state.get("next_meeting_id"))
    if "command" in state:
        summary["has_command"] = isinstance(state.get("command"), dict)
    return summary


class NetworkCommandReader:
    def __init__(self, telemetry):
        self.telemetry = telemetry
        self.enabled = bool(getattr(config, "NETWORK_ENABLED", False))
        self.state_enabled = bool(getattr(config, "STATE_URL", ""))
        self.ota_enabled = bool(getattr(config, "OTA_ENABLED", False))
        self.next_poll_ms = time.ticks_ms()
        self.next_ota_check_ms = time.ticks_add(
            time.ticks_ms(),
            int(getattr(config, "OTA_INITIAL_DELAY_SECONDS", 20) * 1000),
        )
        self.serial_pause_until_ms = 0
        self.failures = 0
        self.wlan = None
        self.last_command = None

        if self.enabled and connect_wifi is None:
            print("Wi-Fi module unavailable; staying in serial mode.")
            self.enabled = False
        if self.state_enabled and fetch_state is None:
            print("State polling unavailable; disabling state polling.")
            self.state_enabled = False
        if self.ota_enabled and check_for_update is None:
            print("OTA module unavailable; disabling OTA.")
            self.ota_enabled = False
        if not self.state_enabled and not self.ota_enabled:
            self.enabled = False
        if not self.enabled:
            print("Network disabled; using USB serial control.")
        self.telemetry.log(
            "network_reader_init",
            enabled=self.enabled,
            state_enabled=self.state_enabled,
            ota_enabled=self.ota_enabled,
        )

    def pause_for_serial_override(self):
        override_seconds = int(getattr(config, "SERIAL_OVERRIDE_SECONDS", 10))
        self.serial_pause_until_ms = time.ticks_add(time.ticks_ms(), override_seconds * 1000)
        self.telemetry.log("serial_override", seconds=override_seconds)

    def poll(self):
        if not self.enabled:
            return None

        now = time.ticks_ms()
        if time.ticks_diff(self.serial_pause_until_ms, now) > 0:
            return None

        ota_due = self.ota_enabled and time.ticks_diff(now, self.next_ota_check_ms) >= 0
        state_due = self.state_enabled and time.ticks_diff(now, self.next_poll_ms) >= 0
        if not ota_due and not state_due:
            return None

        if ota_due:
            self._poll_ota(now)

        if not state_due:
            return None

        return self._poll_state(now)

    def _ensure_wifi(self):
        if self.wlan is None or not self.wlan.isconnected():
            started = time.ticks_ms()
            self.telemetry.log("wifi_connect_start")
            self.wlan = connect_wifi(
                config.WIFI_SSID,
                config.WIFI_PASSWORD,
                getattr(config, "WIFI_CONNECT_TIMEOUT_SECONDS", 20),
            )
            try:
                ip_address = self.wlan.ifconfig()[0]
            except Exception:
                ip_address = ""
            self.telemetry.log(
                "wifi_connected",
                elapsed_ms=time.ticks_diff(time.ticks_ms(), started),
                ip=ip_address,
            )

    def _poll_ota(self, now):
        started = time.ticks_ms()
        try:
            self._ensure_wifi()
            status = check_for_update(
                config.OTA_MANIFEST_URL,
                getattr(config, "OTA_TOKEN", ""),
                getattr(config, "OTA_MAX_FILE_BYTES", 65536),
            )
            if status == "applied":
                return
            self.telemetry.log(
                "ota_poll",
                status=status,
                elapsed_ms=time.ticks_diff(time.ticks_ms(), started),
            )
            self.next_ota_check_ms = time.ticks_add(
                now,
                int(getattr(config, "OTA_CHECK_SECONDS", 300) * 1000),
            )
        except Exception as exc:
            print("OTA check failed:", exc)
            self.telemetry.log(
                "ota_error",
                error=str(exc),
                elapsed_ms=time.ticks_diff(time.ticks_ms(), started),
            )
            self.next_ota_check_ms = time.ticks_add(
                now,
                int(getattr(config, "OTA_ERROR_RETRY_SECONDS", 60) * 1000),
            )

    def _poll_state(self, now):
        started = time.ticks_ms()
        try:
            self._ensure_wifi()

            state = fetch_state(config.STATE_URL, getattr(config, "DEVICE_TOKEN", ""))
            fetched_ms = time.ticks_diff(time.ticks_ms(), started)
            state_info = state_summary(state)
            command = command_from_state(state)
            normalized = normalize_command(command)
            self.failures = 0
            self.next_poll_ms = time.ticks_add(
                now,
                int(getattr(config, "STATE_POLL_SECONDS", 5) * 1000),
            )

            if normalized == self.last_command:
                self.telemetry.log(
                    "state_poll",
                    changed=False,
                    elapsed_ms=time.ticks_diff(time.ticks_ms(), started),
                    fetch_ms=fetched_ms,
                    state=state_info,
                    command=normalized,
                )
                return None
            self.last_command = normalized
            print("Network command:", normalized)
            self.telemetry.log(
                "state_poll",
                changed=True,
                elapsed_ms=time.ticks_diff(time.ticks_ms(), started),
                fetch_ms=fetched_ms,
                state=state_info,
                command=normalized,
            )
            return normalized
        except Exception as exc:
            self.failures += 1
            print("Network poll failed:", exc)
            self.telemetry.log(
                "state_poll_error",
                failures=self.failures,
                error=str(exc),
                elapsed_ms=time.ticks_diff(time.ticks_ms(), started),
            )
            self.next_poll_ms = time.ticks_add(
                now,
                int(getattr(config, "STATE_ERROR_RETRY_SECONDS", 10) * 1000),
            )
            if self.failures >= getattr(config, "NETWORK_ERROR_AFTER_FAILURES", 3):
                command = getattr(config, "NETWORK_ERROR_COMMAND", {"mode": "off"})
                try:
                    normalized = normalize_command(command)
                except Exception:
                    normalized = {"mode": "off"}
                if normalized != self.last_command:
                    self.last_command = normalized
                    self.telemetry.log(
                        "network_fallback_command",
                        failures=self.failures,
                        command=normalized,
                    )
                    return normalized
            return None


class NullTelemetry:
    def log(self, event, **fields):
        pass


main()
