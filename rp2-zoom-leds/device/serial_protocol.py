try:
    import ujson as json
except ImportError:
    import json


VALID_MODES = ("off", "solid", "pulse", "meeting", "meeting_status")
VALID_MEETING_STATES = ("starting_soon", "in_progress", "ending_soon")


class ProtocolError(ValueError):
    pass


def _to_text(line):
    if isinstance(line, bytes):
        return line.decode("utf-8").strip()
    return str(line).strip()


def _clamp_byte(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ProtocolError("RGB values must be integers")

    if number < 0:
        return 0
    if number > 255:
        return 255
    return number


def _as_bool(value):
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _clamp_float(value, default, minimum, maximum):
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default

    if number < minimum:
        return minimum
    if number > maximum:
        return maximum
    return number


def _clamp_int(value, default, minimum, maximum):
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default

    if number < minimum:
        return minimum
    if number > maximum:
        return maximum
    return number


def normalize_rgb(rgb):
    if not isinstance(rgb, (list, tuple)) or len(rgb) != 3:
        raise ProtocolError("RGB must be a list of three values")
    return [_clamp_byte(part) for part in rgb]


def normalize_command(command):
    if not isinstance(command, dict):
        raise ProtocolError("Command must be an object")

    mode = command.get("mode")
    if mode not in VALID_MODES:
        raise ProtocolError("Unsupported mode")

    if mode == "off":
        return {"mode": "off"}

    if mode == "solid":
        return {"mode": "solid", "rgb": normalize_rgb(command.get("rgb"))}

    if mode == "pulse":
        try:
            speed = float(command.get("speed", 0.6))
        except (TypeError, ValueError):
            raise ProtocolError("Pulse speed must be numeric")

        if speed < 0.05:
            speed = 0.05
        if speed > 5.0:
            speed = 5.0

        return {
            "mode": "pulse",
            "rgb": normalize_rgb(command.get("rgb")),
            "speed": speed,
        }

    if mode == "meeting_status":
        state = command.get("state")
        if state not in VALID_MEETING_STATES:
            raise ProtocolError("Unsupported meeting status state")

        minutes = _clamp_float(command.get("minutes", 5), 5.0, 0.0, 120.0)
        has_expected_change = command.get("seconds_until_expected_state_change") is not None
        threshold_default = minutes if has_expected_change else 5.0
        normalized = {
            "mode": "meeting_status",
            "state": state,
            "minutes": minutes,
            "threshold": _clamp_float(
                command.get("threshold", threshold_default),
                threshold_default,
                1.0,
                120.0,
            ),
        }
        if has_expected_change:
            normalized["seconds_until_expected_state_change"] = _clamp_int(
                command.get("seconds_until_expected_state_change"),
                0,
                0,
                24 * 60 * 60,
            )
        return normalized

    try:
        participants = int(command.get("participants", 0))
    except (TypeError, ValueError):
        participants = 0

    if participants < 0:
        participants = 0

    return {
        "mode": "meeting",
        "active": _as_bool(command.get("active", False)),
        "participants": participants,
    }


def parse_line(line):
    text = _to_text(line)
    if not text:
        raise ProtocolError("Empty command")
    return normalize_command(json.loads(text))


def try_parse_line(line):
    try:
        return parse_line(line)
    except (ProtocolError, ValueError):
        return None


def encode_command(command):
    return json.dumps(normalize_command(command)) + "\n"
