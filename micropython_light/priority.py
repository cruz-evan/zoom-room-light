RED = "red"
YELLOW = "yellow"
GREEN = "green"
PURPLE = "purple"


def choose_light(state):
    if not isinstance(state, dict):
        return {
            "color": PURPLE,
            "label": "ERROR",
            "reason": "state was not an object",
        }

    if state.get("in_use"):
        return {
            "color": RED,
            "label": "IN USE",
            "reason": state.get("active_topic") or state.get("last_event") or "active meeting",
        }

    if state.get("next_meeting_id"):
        minutes = state.get("minutes_until_next")
        topic = state.get("next_meeting_topic") or "Scheduled meeting"
        if isinstance(minutes, int):
            reason = "%s starts in %d min" % (topic, minutes)
        else:
            reason = "%s starts soon" % topic
        return {
            "color": YELLOW,
            "label": "STARTS SOON",
            "reason": reason,
        }

    return {
        "color": GREEN,
        "label": "FREE",
        "reason": state.get("last_event") or "free",
    }
