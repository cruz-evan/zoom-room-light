from priority import choose_light


def assert_light(state, expected_color, expected_label):
    actual = choose_light(state)
    assert actual["color"] == expected_color, actual
    assert actual["label"] == expected_label, actual


def test_priority_rules():
    assert_light({"in_use": True, "next_meeting_id": "soon"}, "red", "IN USE")
    assert_light({"in_use": False, "next_meeting_id": "soon"}, "yellow", "STARTS SOON")
    assert_light({"in_use": False, "next_meeting_id": ""}, "green", "FREE")
    assert_light(None, "purple", "ERROR")


if __name__ == "__main__":
    test_priority_rules()
    print("priority tests passed")
