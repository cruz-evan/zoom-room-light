import importlib.util
import sys
import types
from pathlib import Path


class FakeLock:
    def acquire(self):
        pass

    def release(self):
        pass


class FakeReader:
    enabled = True
    state_enabled = True
    failures = 2

    def serial_override_active(self):
        return False


class FakeTelemetry:
    def __init__(self):
        self.events = []

    def log(self, event, **fields):
        self.events.append((event, fields))


class FakeTime:
    def __init__(self, now):
        self.now = now

    def ticks_ms(self):
        return self.now

    @staticmethod
    def ticks_diff(now, start):
        return now - start


class FakeMachine:
    def __init__(self):
        self.reset_count = 0

    def reset(self):
        self.reset_count += 1
        raise RuntimeError("reset called")


def load_device_main(monkeypatch):
    device_dir = Path(__file__).resolve().parents[1] / "device"
    monkeypatch.syspath_prepend(str(device_dir))
    monkeypatch.setitem(sys.modules, "machine", types.SimpleNamespace(reset=lambda: None))
    monkeypatch.setitem(sys.modules, "config", types.SimpleNamespace())
    monkeypatch.setitem(
        sys.modules,
        "led_strip",
        types.SimpleNamespace(LedStrip=object, StatusLed=object),
    )
    monkeypatch.setitem(
        sys.modules,
        "serial_protocol",
        types.SimpleNamespace(normalize_command=lambda command: command, try_parse_line=lambda line: None),
    )
    monkeypatch.setitem(
        sys.modules,
        "state_mapper",
        types.SimpleNamespace(command_from_state=lambda state: {"mode": "off"}),
    )
    monkeypatch.setitem(
        sys.modules,
        "state_client",
        types.SimpleNamespace(fetch_state=lambda *args, **kwargs: {}),
    )
    monkeypatch.setitem(
        sys.modules,
        "wifi_connect",
        types.SimpleNamespace(connect_wifi_profiles=lambda *args, **kwargs: None),
    )
    monkeypatch.setitem(
        sys.modules,
        "wifi_profiles",
        types.SimpleNamespace(profiles_from_config=lambda config: ()),
    )

    spec = importlib.util.spec_from_file_location("device_main_watchdog_test", device_dir / "main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_threaded_reader(module, now=0, last_heartbeat_ms=0, last_state_response_ms=0):
    threaded = object.__new__(module.ThreadedNetworkCommandReader)
    threaded.reader = FakeReader()
    threaded.reader.last_state_response_ms = last_state_response_ms
    threaded.telemetry = FakeTelemetry()
    threaded.lock = FakeLock()
    threaded.pending_command = {"mode": "off"}
    threaded.watchdog_enabled = True
    threaded.watchdog_ms = 30000
    threaded.last_heartbeat_ms = last_heartbeat_ms
    threaded.resetting = False
    module.time = FakeTime(now)
    return threaded


def test_network_thread_watchdog_allows_fresh_heartbeat(monkeypatch):
    module = load_device_main(monkeypatch)
    machine = FakeMachine()
    module.machine = machine
    threaded = make_threaded_reader(module, now=25000, last_heartbeat_ms=0, last_state_response_ms=0)

    command = threaded.poll()

    assert command == {"mode": "off"}
    assert machine.reset_count == 0
    assert threaded.telemetry.events == []


def test_network_thread_watchdog_resets_stale_thread(monkeypatch):
    module = load_device_main(monkeypatch)
    machine = FakeMachine()
    sleeps = []
    marker_writes = []
    module.machine = machine
    module._sleep_ms = sleeps.append
    module.mark_watchdog_reset_pending = lambda: marker_writes.append(True)
    threaded = make_threaded_reader(module, now=31000, last_heartbeat_ms=0)

    try:
        threaded.poll()
    except RuntimeError as exc:
        assert str(exc) == "reset called"
    else:
        raise AssertionError("expected machine.reset() to be called")

    assert machine.reset_count == 1
    assert marker_writes == [True]
    assert sleeps == [250]
    assert threaded.telemetry.events == [
        (
            "network_thread_watchdog_reset",
            {
                "reason": "thread_heartbeat",
                "stale_ms": 31000,
                "watchdog_seconds": 30,
                "reader_enabled": True,
                "state_enabled": True,
                "reader_failures": 2,
            },
        )
    ]


def test_network_thread_watchdog_resets_stale_state_response(monkeypatch):
    module = load_device_main(monkeypatch)
    machine = FakeMachine()
    sleeps = []
    marker_writes = []
    module.machine = machine
    module._sleep_ms = sleeps.append
    module.mark_watchdog_reset_pending = lambda: marker_writes.append(True)
    threaded = make_threaded_reader(
        module,
        now=31000,
        last_heartbeat_ms=31000,
        last_state_response_ms=0,
    )

    try:
        threaded.poll()
    except RuntimeError as exc:
        assert str(exc) == "reset called"
    else:
        raise AssertionError("expected machine.reset() to be called")

    assert machine.reset_count == 1
    assert marker_writes == [True]
    assert sleeps == [250]
    assert threaded.telemetry.events == [
        (
            "network_thread_watchdog_reset",
            {
                "reason": "state_response",
                "stale_ms": 31000,
                "watchdog_seconds": 30,
                "reader_enabled": True,
                "state_enabled": True,
                "reader_failures": 2,
            },
        )
    ]


def test_watchdog_reset_marker_is_consumed_once(monkeypatch, tmp_path):
    module = load_device_main(monkeypatch)
    monkeypatch.chdir(tmp_path)

    module.mark_watchdog_reset_pending()

    assert (tmp_path / module.WATCHDOG_RESET_MARKER).read_text(encoding="utf-8") == "1\n"
    assert module.consume_watchdog_reset_pending() is True
    assert module.consume_watchdog_reset_pending() is False
    assert not (tmp_path / module.WATCHDOG_RESET_MARKER).exists()


def test_ota_config_marker_is_consumed_once(monkeypatch, tmp_path):
    module = load_device_main(monkeypatch)
    monkeypatch.chdir(tmp_path)

    module.mark_ota_config_check_pending()

    assert (tmp_path / module.OTA_CONFIG_CHECK_MARKER).read_text(encoding="utf-8") == "1\n"
    assert module.consume_ota_config_check_pending() is True
    assert module.consume_ota_config_check_pending() is False
    assert not (tmp_path / module.OTA_CONFIG_CHECK_MARKER).exists()


def test_state_request_marker_is_consumed_once(monkeypatch, tmp_path):
    module = load_device_main(monkeypatch)
    monkeypatch.chdir(tmp_path)

    module.mark_state_request_pending()

    assert (tmp_path / module.STATE_REQUEST_MARKER).read_text(encoding="utf-8") == "1\n"
    assert module.consume_state_request_pending() is True
    assert module.consume_state_request_pending() is False
    assert not (tmp_path / module.STATE_REQUEST_MARKER).exists()
