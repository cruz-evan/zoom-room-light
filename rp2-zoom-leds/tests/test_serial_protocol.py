import json

import pytest

from device.serial_protocol import (
    ProtocolError,
    encode_command,
    normalize_command,
    normalize_rgb,
    parse_line,
    try_parse_line,
)


def test_encode_command_outputs_json_line():
    encoded = encode_command({"mode": "solid", "rgb": [255, 0, 0]})

    assert encoded.endswith("\n")
    assert json.loads(encoded) == {"mode": "solid", "rgb": [255, 0, 0]}


def test_parse_meeting_command_from_bytes():
    command = parse_line(b'{"mode":"meeting","active":true,"participants":3}\n')

    assert command == {"mode": "meeting", "active": True, "participants": 3}


def test_parse_meeting_status_command():
    command = parse_line(
        b'{"mode":"meeting_status","state":"ending_soon","minutes":4.5}\n'
    )

    assert command == {
        "mode": "meeting_status",
        "state": "ending_soon",
        "minutes": 4.5,
        "threshold": 5.0,
    }


def test_parse_meeting_status_preserves_expected_state_change_seconds():
    command = parse_line(
        b'{"mode":"meeting_status","state":"starting_soon","minutes":15,'
        b'"seconds_until_expected_state_change":450}\n'
    )

    assert command == {
        "mode": "meeting_status",
        "state": "starting_soon",
        "minutes": 15.0,
        "threshold": 15.0,
        "seconds_until_expected_state_change": 450,
    }


def test_meeting_status_rejects_unknown_state():
    with pytest.raises(ProtocolError):
        normalize_command({"mode": "meeting_status", "state": "break"})


def test_rgb_values_are_clamped():
    assert normalize_rgb([-10, 120, 999]) == [0, 120, 255]


def test_pulse_speed_is_clamped():
    command = normalize_command({"mode": "pulse", "rgb": [0, 120, 255], "speed": 99})

    assert command["speed"] == 5.0


def test_invalid_mode_raises_protocol_error():
    with pytest.raises(ProtocolError):
        normalize_command({"mode": "sparkle"})


def test_try_parse_line_returns_none_for_bad_json():
    assert try_parse_line("{not-json}") is None


def test_startup_sequence_commands_cover_supported_light_modes():
    from device import config

    commands = [normalize_command(command) for command in config.STARTUP_SEQUENCE_COMMANDS]
    modes = {command["mode"] for command in commands}

    assert modes == {"solid", "pulse", "meeting", "meeting_status", "off"}
