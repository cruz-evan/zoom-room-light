import sys
import time
import machine

try:
    import _thread
except ImportError:
    _thread = None

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
    from wifi_connect import connect_wifi_profiles
    from wifi_profiles import profiles_from_config
except ImportError:
    fetch_state = None
    connect_wifi_profiles = None
    profiles_from_config = None

try:
    from ota_client import check_for_update
except Exception:
    check_for_update = None

try:
    from ota_config import check_for_config_update, config_url_from_manifest_url
except Exception:
    check_for_config_update = None
    config_url_from_manifest_url = None

try:
    from telemetry import from_config as telemetry_from_config
except Exception:
    telemetry_from_config = None

try:
    from resource_monitor import (
        NullResourceMonitor,
        from_config as resource_monitor_from_config,
    )
except Exception:
    NullResourceMonitor = None
    resource_monitor_from_config = None


def _sleep_ms(delay_ms):
    if hasattr(time, "sleep_ms"):
        time.sleep_ms(delay_ms)
    else:
        time.sleep(delay_ms / 1000)


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
    strip = LedStrip(
        config.LED_PIN,
        config.LED_COUNT,
        config.BRIGHTNESS,
        getattr(config, "LED_MAX_REFRESH_FPS", 30),
    )
    status = StatusLed()
    reader = UsbCommandReader()
    telemetry = telemetry_from_config(config) if telemetry_from_config else NullTelemetry()
    resource_monitor = (
        resource_monitor_from_config(config, telemetry)
        if resource_monitor_from_config
        else NullResourceMonitor()
    )
    telemetry.log(
        "boot",
        device_id=str(getattr(config, "DEVICE_ID", "")),
        room_id=str(getattr(config, "ROOM_ID", "")),
        hostname=str(getattr(config, "DEVICE_HOSTNAME", "")),
        led_pin=int(getattr(config, "LED_PIN", 0)),
        led_count=int(getattr(config, "LED_COUNT", 0)),
        led_max_refresh_fps=int(getattr(config, "LED_MAX_REFRESH_FPS", 0)),
        network_enabled=bool(getattr(config, "NETWORK_ENABLED", False)),
        state_poll_seconds=int(getattr(config, "STATE_POLL_SECONDS", 0)),
        ota_check_seconds=int(getattr(config, "OTA_CHECK_SECONDS", 0)),
        network_diagnostic_seconds=int(getattr(config, "NETWORK_DIAGNOSTIC_SECONDS", 0)),
        loop_delay_ms=int(getattr(config, "LOOP_DELAY_MS", 0)),
        resource_monitor_enabled=bool(getattr(config, "RESOURCE_MONITOR_ENABLED", False)),
        resource_monitor_sample_seconds=int(getattr(config, "RESOURCE_MONITOR_SAMPLE_SECONDS", 0)),
    )
    network_reader = NetworkCommandReader(telemetry)
    startup_command = run_startup_sequence(strip, status, telemetry, network_reader)
    if startup_command is not None:
        apply_command(strip, status, startup_command, telemetry, "startup_state")
    run_startup_self_test(strip, status, telemetry)
    network = create_network_command_reader(telemetry, network_reader)

    while True:
        loop_started_ms = time.ticks_ms()
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

        resource_monitor.observe_loop(
            time.ticks_diff(time.ticks_ms(), loop_started_ms),
            config.LOOP_DELAY_MS,
        )
        _sleep_ms(config.LOOP_DELAY_MS)


def run_startup_sequence(strip, status, telemetry, network):
    if not getattr(config, "STARTUP_SEQUENCE_ENABLED", False):
        return

    requires_network = bool(getattr(config, "STARTUP_SEQUENCE_REQUIRES_NETWORK", True))
    startup_command = None
    if requires_network:
        connected, startup_command = network.confirm_startup_connectivity()
        if not connected:
            telemetry.log("startup_sequence_skip", reason="network_not_confirmed")
            return None

    step_ms = int(getattr(config, "STARTUP_SEQUENCE_STEP_MS", 900))
    commands = getattr(config, "STARTUP_SEQUENCE_COMMANDS", ())

    telemetry.log(
        "startup_sequence_start",
        steps=len(commands),
        step_ms=step_ms,
        network_confirmed=requires_network,
    )
    _play_command_sequence(strip, status, telemetry, commands, step_ms, "startup_sequence")
    telemetry.log("startup_sequence_done")
    return startup_command


def run_startup_self_test(strip, status, telemetry):
    if not getattr(config, "STARTUP_SELF_TEST", False):
        return

    step_ms = int(getattr(config, "STARTUP_SELF_TEST_STEP_MS", 2000))
    commands = getattr(config, "STARTUP_SELF_TEST_COMMANDS", ())

    telemetry.log("self_test_start", steps=len(commands), step_ms=step_ms)
    _play_command_sequence(strip, status, telemetry, commands, step_ms, "self_test")
    telemetry.log("self_test_done")


def _play_command_sequence(strip, status, telemetry, commands, step_ms, source):
    for command in commands:
        ok = apply_command(strip, status, command, telemetry, source)
        if ok:
            status.set(0)

        started = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), started) < step_ms:
            strip.tick()
            if not strip.available:
                status.tick_heartbeat(config.STATUS_BLINK_MS)
            _sleep_ms(config.LOOP_DELAY_MS)


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


def create_network_command_reader(telemetry, reader=None):
    if reader is None:
        reader = NetworkCommandReader(telemetry)
    thread_enabled = bool(getattr(config, "NETWORK_THREAD_ENABLED", True))

    if not reader.enabled or not thread_enabled:
        return reader

    if _thread is None:
        print("Network thread unavailable; state polling may pause animations.")
        telemetry.log("network_thread_unavailable")
        return reader

    try:
        return ThreadedNetworkCommandReader(reader, telemetry)
    except Exception as exc:
        print("Network thread unavailable:", exc)
        telemetry.log("network_thread_error", error=str(exc))
        return reader


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
        "last_event",
        "updated_at",
        "source",
        "poll_seconds",
    ):
        if key in state:
            summary[key] = state.get(key)
    if "next_meeting_id" in state:
        summary["has_next_meeting"] = bool(state.get("next_meeting_id"))
    if "command" in state:
        summary["has_command"] = isinstance(state.get("command"), dict)
    return summary


def positive_seconds(value, default, minimum=1, maximum=300):
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        seconds = int(default)
    if seconds < minimum:
        return minimum
    if seconds > maximum:
        return maximum
    return seconds


def state_poll_seconds_from_response(state):
    default = int(getattr(config, "STATE_POLL_SECONDS", 60))
    if not isinstance(state, dict):
        return default
    return positive_seconds(state.get("poll_seconds"), default)


class ThreadedNetworkCommandReader:
    def __init__(self, reader, telemetry):
        self.reader = reader
        self.telemetry = telemetry
        self.lock = _thread.allocate_lock()
        self.pending_command = None
        self.idle_ms = int(getattr(config, "NETWORK_THREAD_IDLE_MS", 20))
        self.last_status_ms = 0
        _thread.start_new_thread(self._run, ())
        print("Network polling running on background thread.")
        self.telemetry.log("network_thread_start", idle_ms=self.idle_ms)

    def pause_for_serial_override(self):
        self.reader.pause_for_serial_override()
        self._set_pending_command(None)

    def poll(self):
        self.lock.acquire()
        try:
            command = getattr(self, "pending_command", None)
            self.pending_command = None
            return command
        finally:
            self.lock.release()

    def _set_pending_command(self, command):
        self.lock.acquire()
        try:
            self.pending_command = command
        finally:
            self.lock.release()

    def _log_thread_status(self, now):
        interval_seconds = int(getattr(config, "NETWORK_DIAGNOSTIC_SECONDS", 30))
        if interval_seconds <= 0:
            return
        if self.last_status_ms and time.ticks_diff(now, self.last_status_ms) < interval_seconds * 1000:
            return

        self.last_status_ms = now
        self.telemetry.log(
            "network_thread_status",
            idle_ms=self.idle_ms,
            pending_command=getattr(self, "pending_command", None) is not None,
            reader_enabled=getattr(self.reader, "enabled", False),
            reader_failures=getattr(self.reader, "failures", 0),
        )

    def _run(self):
        while True:
            try:
                self._log_thread_status(time.ticks_ms())
                command = self.reader.poll()
                if command is not None and not self.reader.serial_override_active():
                    self._set_pending_command(command)
            except Exception as exc:
                print("Network thread failed:", exc)
                self.telemetry.log("network_thread_error", error=str(exc))
                _sleep_ms(int(getattr(config, "STATE_ERROR_RETRY_SECONDS", 10) * 1000))
                continue

            _sleep_ms(self.idle_ms)


class NetworkCommandReader:
    def __init__(self, telemetry):
        self.telemetry = telemetry
        self.enabled = bool(getattr(config, "NETWORK_ENABLED", False))
        self.state_enabled = bool(getattr(config, "STATE_URL", ""))
        self.ota_enabled = bool(getattr(config, "OTA_ENABLED", False))
        self.ota_config_enabled = bool(getattr(config, "OTA_CONFIG_ENABLED", False))
        self.next_poll_ms = time.ticks_ms()
        self.next_ota_check_ms = time.ticks_add(
            time.ticks_ms(),
            int(getattr(config, "OTA_INITIAL_DELAY_SECONDS", 20) * 1000),
        )
        self.serial_pause_until_ms = 0
        self.failures = 0
        self.wlan = None
        self.last_command = None
        self.last_command_emitted_ms = 0
        self.last_diagnostic_ms = 0

        if self.enabled and (connect_wifi_profiles is None or profiles_from_config is None):
            print("Wi-Fi module unavailable; staying in serial mode.")
            self.enabled = False
        if self.state_enabled and fetch_state is None:
            print("State polling unavailable; disabling state polling.")
            self.state_enabled = False
        if self.ota_enabled and check_for_update is None:
            print("OTA module unavailable; disabling OTA.")
            self.ota_enabled = False
        if self.ota_config_enabled and check_for_config_update is None:
            print("OTA config module unavailable; disabling OTA config.")
            self.ota_config_enabled = False
        if not self.state_enabled and not self.ota_enabled and not self.ota_config_enabled:
            self.enabled = False
        if not self.enabled:
            print("Network disabled; using USB serial control.")
        self.telemetry.log(
            "network_reader_init",
            enabled=self.enabled,
            state_enabled=self.state_enabled,
            ota_enabled=self.ota_enabled,
            ota_config_enabled=self.ota_config_enabled,
            state_poll_seconds=int(getattr(config, "STATE_POLL_SECONDS", 0)),
            ota_check_seconds=int(getattr(config, "OTA_CHECK_SECONDS", 0)),
        )

    def pause_for_serial_override(self):
        override_seconds = int(getattr(config, "SERIAL_OVERRIDE_SECONDS", 10))
        self.serial_pause_until_ms = time.ticks_add(time.ticks_ms(), override_seconds * 1000)
        self.last_command = None
        self.last_command_emitted_ms = 0
        self.telemetry.log("serial_override", seconds=override_seconds)

    def serial_override_active(self):
        return time.ticks_diff(self.serial_pause_until_ms, time.ticks_ms()) > 0

    def _log_poll_diagnostic(self, now, state_due=False, ota_due=False, serial_override=False):
        interval_seconds = int(getattr(config, "NETWORK_DIAGNOSTIC_SECONDS", 30))
        if interval_seconds <= 0:
            return
        if self.last_diagnostic_ms and time.ticks_diff(now, self.last_diagnostic_ms) < interval_seconds * 1000:
            return

        self.last_diagnostic_ms = now
        wlan_connected = False
        try:
            wlan_connected = bool(self.wlan and self.wlan.isconnected())
        except Exception:
            wlan_connected = False

        self.telemetry.log(
            "network_poll_status",
            enabled=self.enabled,
            state_enabled=self.state_enabled,
            ota_enabled=self.ota_enabled,
            ota_config_enabled=self.ota_config_enabled,
            serial_override=serial_override,
            state_due=state_due,
            ota_due=ota_due,
            state_due_ms=time.ticks_diff(self.next_poll_ms, now),
            ota_due_ms=time.ticks_diff(self.next_ota_check_ms, now),
            failures=self.failures,
            wlan_connected=wlan_connected,
        )

    def confirm_startup_connectivity(self):
        if not self.enabled:
            return False, None

        started = time.ticks_ms()
        try:
            self._ensure_wifi()

            if self.state_enabled:
                state = fetch_state(
                    config.STATE_URL,
                    getattr(config, "DEVICE_TOKEN", ""),
                    getattr(config, "DEVICE_ID", ""),
                    getattr(config, "STATE_REQUEST_TIMEOUT_SECONDS", 4),
                )
                fetched_ms = time.ticks_diff(time.ticks_ms(), started)
                state_info = state_summary(state)
                poll_seconds = state_poll_seconds_from_response(state)
                command = command_from_state(state)
                normalized = normalize_command(command)
                self.failures = 0
                self.last_command = normalized
                self.last_command_emitted_ms = time.ticks_ms()
                self.next_poll_ms = time.ticks_add(
                    time.ticks_ms(),
                    int(poll_seconds * 1000),
                )
                self.telemetry.log(
                    "startup_connectivity_ok",
                    via="state",
                    poll_seconds=poll_seconds,
                    elapsed_ms=time.ticks_diff(time.ticks_ms(), started),
                    fetch_ms=fetched_ms,
                    state=state_info,
                    command=normalized,
                )
                return True, normalized

            if self.ota_enabled or self.ota_config_enabled:
                status = self._check_ota()
                self.next_ota_check_ms = time.ticks_add(
                    time.ticks_ms(),
                    int(getattr(config, "OTA_CHECK_SECONDS", 60) * 1000),
                )
                self.telemetry.log(
                    "startup_connectivity_ok",
                    via="ota",
                    status=status,
                    elapsed_ms=time.ticks_diff(time.ticks_ms(), started),
                )
                return True, None
        except Exception as exc:
            self.telemetry.log(
                "startup_connectivity_error",
                error=str(exc),
                elapsed_ms=time.ticks_diff(time.ticks_ms(), started),
            )
            return False, None

        return False, None

    def poll(self):
        if not self.enabled:
            return None

        now = time.ticks_ms()
        if self.serial_override_active():
            self._log_poll_diagnostic(now, serial_override=True)
            return None

        ota_due = (
            (self.ota_enabled or self.ota_config_enabled)
            and time.ticks_diff(now, self.next_ota_check_ms) >= 0
        )
        state_due = self.state_enabled and time.ticks_diff(now, self.next_poll_ms) >= 0
        self._log_poll_diagnostic(now, state_due=state_due, ota_due=ota_due)
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
            self.wlan = connect_wifi_profiles(
                profiles_from_config(config),
                getattr(config, "WIFI_CONNECT_TIMEOUT_SECONDS", 20),
                getattr(config, "DEVICE_HOSTNAME", "auto"),
                getattr(config, "DEVICE_HOSTNAME_PREFIX", "zoom-light"),
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
            status = self._check_ota()
            self.telemetry.log(
                "ota_poll",
                status=status,
                elapsed_ms=time.ticks_diff(time.ticks_ms(), started),
            )
            self.next_ota_check_ms = time.ticks_add(
                now,
                int(getattr(config, "OTA_CHECK_SECONDS", 60) * 1000),
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

    def _check_ota(self):
        statuses = []
        if self.ota_enabled:
            status = check_for_update(
                config.OTA_MANIFEST_URL,
                getattr(config, "OTA_TOKEN", ""),
                getattr(config, "OTA_MAX_FILE_BYTES", 65536),
            )
            statuses.append("app:%s" % status)
            if status == "applied":
                return "app:applied"

        if self.ota_config_enabled:
            config_url = str(getattr(config, "OTA_CONFIG_URL", "") or "")
            if not config_url and config_url_from_manifest_url is not None:
                config_url = config_url_from_manifest_url(getattr(config, "OTA_MANIFEST_URL", ""))
            status = check_for_config_update(
                config_url,
                getattr(config, "OTA_CONFIG_KEY", ""),
                getattr(config, "OTA_TOKEN", ""),
            )
            statuses.append("config:%s" % status)
            if status == "applied":
                self.telemetry.log("ota_config_applied")
                _sleep_ms(250)
                machine.reset()
                return "config:applied"

        return ",".join(statuses) if statuses else "disabled"

    def _poll_state(self, now):
        started = time.ticks_ms()
        try:
            self._ensure_wifi()

            state = fetch_state(
                config.STATE_URL,
                getattr(config, "DEVICE_TOKEN", ""),
                getattr(config, "DEVICE_ID", ""),
                getattr(config, "STATE_REQUEST_TIMEOUT_SECONDS", 4),
            )
            fetched_ms = time.ticks_diff(time.ticks_ms(), started)
            state_info = state_summary(state)
            poll_seconds = state_poll_seconds_from_response(state)
            command = command_from_state(state)
            normalized = normalize_command(command)
            self.failures = 0
            self.next_poll_ms = time.ticks_add(
                now,
                int(poll_seconds * 1000),
            )

            if normalized == self.last_command:
                reapply_seconds = int(getattr(config, "STATE_REAPPLY_SECONDS", 60))
                reapply_due = (
                    reapply_seconds > 0
                    and time.ticks_diff(time.ticks_ms(), self.last_command_emitted_ms)
                    >= reapply_seconds * 1000
                )
                if reapply_due:
                    self.last_command_emitted_ms = time.ticks_ms()
                    self.telemetry.log(
                        "state_poll",
                        changed=False,
                        reapply=True,
                        poll_seconds=poll_seconds,
                        elapsed_ms=time.ticks_diff(time.ticks_ms(), started),
                        fetch_ms=fetched_ms,
                        state=state_info,
                        command=normalized,
                    )
                    return normalized
                self.telemetry.log(
                    "state_poll",
                    changed=False,
                    reapply=False,
                    poll_seconds=poll_seconds,
                    elapsed_ms=time.ticks_diff(time.ticks_ms(), started),
                    fetch_ms=fetched_ms,
                    state=state_info,
                    command=normalized,
                )
                return None
            self.last_command = normalized
            self.last_command_emitted_ms = time.ticks_ms()
            print("Network command:", normalized)
            self.telemetry.log(
                "state_poll",
                changed=True,
                reapply=False,
                poll_seconds=poll_seconds,
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
                    self.last_command_emitted_ms = time.ticks_ms()
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


if NullResourceMonitor is None:
    class NullResourceMonitor:
        def observe_loop(self, active_ms, sleep_ms=None):
            pass

        def log_sample(self, now_ms=None, sleep_ms=None):
            pass


main()
