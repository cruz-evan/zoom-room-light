import importlib
import sys
import types


class FakeMachine:
    @staticmethod
    def unique_id():
        return b"\xe6d0\xa6K*\x8d2"


def load_config_with_secrets(machine_module=FakeMachine, **values):
    previous_secrets = sys.modules.get("secrets")
    previous_machine = sys.modules.get("machine")
    sys.modules["secrets"] = types.SimpleNamespace(**values)
    if machine_module is None:
        sys.modules.pop("machine", None)
    else:
        sys.modules["machine"] = machine_module
    sys.modules.pop("device.config", None)

    try:
        return importlib.import_module("device.config")
    finally:
        sys.modules.pop("device.config", None)
        if previous_secrets is None:
            sys.modules.pop("secrets", None)
        else:
            sys.modules["secrets"] = previous_secrets
        if previous_machine is None:
            sys.modules.pop("machine", None)
        else:
            sys.modules["machine"] = previous_machine


def test_auto_device_id_uses_machine_unique_id():
    config = load_config_with_secrets(DEVICE_ID="auto")

    assert config.BOARD_UNIQUE_ID == "e66430a64b2a8d32"
    assert config.DEVICE_ID == "pico-e66430a64b2a8d32"


def test_legacy_placeholder_device_ids_are_treated_as_auto():
    config = load_config_with_secrets(DEVICE_ID="zoom-led-pico")

    assert config.DEVICE_ID == "pico-e66430a64b2a8d32"


def test_legacy_placeholder_telemetry_id_is_treated_as_auto():
    config = load_config_with_secrets(
        DEVICE_ID="auto",
        TELEMETRY_DEVICE_ID="zoom-led-pico",
    )

    assert config.TELEMETRY_DEVICE_ID == "pico-e66430a64b2a8d32"


def test_explicit_device_id_still_wins():
    config = load_config_with_secrets(DEVICE_ID="board-room-a")

    assert config.DEVICE_ID == "board-room-a"


def test_led_hardware_is_selected_by_device_id():
    config = load_config_with_secrets(
        DEVICE_ID="board-room-b",
        DEVICE_HARDWARE={
            "board-room-a": {"led_pin": 0, "led_count": 144},
            "board-room-b": {"led_pin": 2, "led_count": 96, "brightness": 0.2},
        },
    )

    assert config.LED_PIN == 2
    assert config.LED_COUNT == 96
    assert config.BRIGHTNESS == 0.2


def test_direct_led_pin_override_still_works_without_hardware_map():
    config = load_config_with_secrets(
        DEVICE_ID="board-room-c",
        LED_PIN=4,
        LED_COUNT=120,
    )

    assert config.LED_PIN == 4
    assert config.LED_COUNT == 120


def test_hardware_map_falls_back_to_safe_defaults_for_unknown_device():
    config = load_config_with_secrets(
        DEVICE_ID="unknown-board",
        DEVICE_HARDWARE={
            "board-room-a": {"led_pin": 0, "led_count": 144},
        },
    )

    assert config.LED_PIN == 0
    assert config.LED_COUNT == 144


def test_default_refresh_rate_is_capped_at_30fps():
    config = load_config_with_secrets()

    assert config.LED_MAX_REFRESH_FPS == 30
    assert config.LOOP_DELAY_MS == 34


def test_refresh_rate_settings_can_be_overridden_from_secrets():
    config = load_config_with_secrets(LED_MAX_REFRESH_FPS=24, LOOP_DELAY_MS=42)

    assert config.LED_MAX_REFRESH_FPS == 24
    assert config.LOOP_DELAY_MS == 42


def test_resource_monitor_defaults_to_disabled_even_when_telemetry_enabled():
    config = load_config_with_secrets(TELEMETRY_ENABLED=True)

    assert config.RESOURCE_MONITOR_ENABLED is False
    assert config.RESOURCE_MONITOR_SAMPLE_SECONDS == 10
    assert config.RESOURCE_MONITOR_CPU_WARN_PERCENT == 80
    assert config.RESOURCE_MONITOR_MIN_FREE_BYTES == 24000


def test_resource_monitor_can_be_disabled_separately():
    config = load_config_with_secrets(
        TELEMETRY_ENABLED=True,
        RESOURCE_MONITOR_ENABLED=False,
    )

    assert config.RESOURCE_MONITOR_ENABLED is False


def test_ota_config_is_disabled_by_default_even_when_configured():
    config = load_config_with_secrets(
        WIFI_SSID="wifi",
        OTA_MANIFEST_URL="https://example.test/manifest.json",
        OTA_CONFIG_KEY="secret",
    )

    assert config.OTA_CONFIG_ENABLED is False


def test_app_ota_is_disabled_by_default_even_when_configured():
    config = load_config_with_secrets(
        WIFI_SSID="wifi",
        OTA_MANIFEST_URL="https://example.test/manifest.json",
    )

    assert config.OTA_ENABLED is False


def test_app_ota_can_be_explicitly_enabled():
    config = load_config_with_secrets(
        WIFI_SSID="wifi",
        OTA_MANIFEST_URL="https://example.test/manifest.json",
        OTA_ENABLED=True,
    )

    assert config.OTA_ENABLED is True


def test_hardware_watchdog_is_enabled_by_default():
    config = load_config_with_secrets()

    assert config.HARDWARE_WATCHDOG_ENABLED is True


def test_hardware_watchdog_can_be_disabled_for_bench_debugging():
    config = load_config_with_secrets(HARDWARE_WATCHDOG_ENABLED=False)

    assert config.HARDWARE_WATCHDOG_ENABLED is False


def test_ota_config_can_be_explicitly_enabled():
    config = load_config_with_secrets(
        WIFI_SSID="wifi",
        OTA_MANIFEST_URL="https://example.test/manifest.json",
        OTA_CONFIG_KEY="secret",
        OTA_CONFIG_ENABLED=True,
    )

    assert config.OTA_CONFIG_ENABLED is True


def test_wifi_fallback_secret_names_are_exposed():
    config = load_config_with_secrets(
        OUTPOST_WIFI_SSID="outpost",
        OUTPOST_WIFI_PASSWORD="outpost-secret",
        WIFI_FALLBACK_SSID="fallback",
        WIFI_FALLBACK_PASSWORD="fallback-secret",
        PHONE_HOTSPOT_SSID="phone",
        PHONE_HOTSPOT_PASSWORD="phone-secret",
    )

    assert config.OUTPOST_WIFI_SSID == "outpost"
    assert config.OUTPOST_WIFI_PASSWORD == "outpost-secret"
    assert config.WIFI_FALLBACK_SSID == "fallback"
    assert config.WIFI_FALLBACK_PASSWORD == "fallback-secret"
    assert config.PHONE_HOTSPOT_SSID == "phone"
    assert config.PHONE_HOTSPOT_PASSWORD == "phone-secret"


def test_ota_config_can_use_outpost_wifi_without_generic_primary():
    config = load_config_with_secrets(
        OUTPOST_WIFI_SSID="outpost",
        OTA_MANIFEST_URL="https://example.test/manifest.json",
        OTA_CONFIG_KEY="secret",
        OTA_CONFIG_ENABLED=True,
    )

    assert config.WIFI_CONFIGURED is True
    assert config.OTA_CONFIG_ENABLED is True
