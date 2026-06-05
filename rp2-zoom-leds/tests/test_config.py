import importlib
import sys
import types


def load_config_with_secrets(**values):
    previous_secrets = sys.modules.get("secrets")
    sys.modules["secrets"] = types.SimpleNamespace(**values)
    sys.modules.pop("device.config", None)

    try:
        return importlib.import_module("device.config")
    finally:
        sys.modules.pop("device.config", None)
        if previous_secrets is None:
            sys.modules.pop("secrets", None)
        else:
            sys.modules["secrets"] = previous_secrets


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
