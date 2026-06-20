def command_from_state(state):
    if not isinstance(state, dict):
        raise ValueError("state must be an object")

    if isinstance(state.get("command"), dict):
        return state["command"]

    meeting_status = state.get("meeting_status")
    if meeting_status:
        return command_from_meeting_status(meeting_status, state.get("minutes"))

    if state.get("in_use"):
        minutes_until_end = state.get("minutes_until_end")
        if minutes_until_end is not None:
            return {
                "mode": "meeting_status",
                "state": "ending_soon",
                "minutes": _minutes(minutes_until_end),
            }
        return {"mode": "meeting_status", "state": "in_progress"}

    if state.get("next_meeting_id"):
        return {
            "mode": "meeting_status",
            "state": "starting_soon",
            "minutes": _minutes(state.get("minutes_until_next")),
        }

    color = str(state.get("color") or "").lower()
    label = str(state.get("label") or "").lower()
    if color == "yellow" or "starts soon" in label:
        return {
            "mode": "meeting_status",
            "state": "starting_soon",
            "minutes": _minutes(state.get("minutes_until_next")),
        }
    if color == "orange" or "ending soon" in label:
        return {
            "mode": "meeting_status",
            "state": "ending_soon",
            "minutes": _minutes(state.get("minutes_until_end")),
        }
    if color in ("red", "green") or label in ("in use", "free"):
        return {"mode": "meeting_status", "state": "in_progress"} if color == "red" else {"mode": "off"}

    return {"mode": "off"}


def command_from_meeting_status(state, minutes=None):
    if state == "off":
        return {"mode": "off"}
    if state in ("starting_soon", "ending_soon"):
        return {"mode": "meeting_status", "state": state, "minutes": _minutes(minutes)}
    if state == "in_progress":
        return {"mode": "meeting_status", "state": "in_progress"}
    raise ValueError("unsupported meeting_status")


def _minutes(value):
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        minutes = 5
    if minutes < 0:
        return 0
    if minutes > 120:
        return 120
    return minutes

#Test comment for OTA