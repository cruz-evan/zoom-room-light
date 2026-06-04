from zoom_light.light_state import LightController


def test_schedule_priority_rules() -> None:
    light = LightController()

    state = light.update_schedule_warning(
        meeting_id="next",
        topic="Next meeting",
        start_time="2026-06-04T18:00:00Z",
        minutes_until_start=5,
    )
    assert state["color"] == "yellow"
    assert state["label"] == "STARTS SOON"

    state = light.update_schedule_active(
        meeting_id="current",
        topic="Current meeting",
        end_time="2026-06-04T18:30:00Z",
        minutes_until_end=None,
    )
    assert state["color"] == "red"
    assert state["label"] == "IN USE"
    assert state["next_meeting_id"] == ""

    state = light.update_schedule_active(
        meeting_id="current",
        topic="Current meeting",
        end_time="2026-06-04T18:30:00Z",
        minutes_until_end=5,
    )
    assert state["color"] == "orange"
    assert state["label"] == "ENDING SOON"

    light.update_schedule_warning(
        meeting_id="next",
        topic="Next meeting",
        start_time="2026-06-04T18:31:00Z",
        minutes_until_start=5,
    )
    state = light.update_schedule_inactive()
    assert state["color"] == "yellow"
    assert state["label"] == "STARTS SOON"
