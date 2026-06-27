from device.state_mapper import command_from_state


def test_command_from_state_carries_expected_state_change_seconds():
    command = command_from_state(
        {
            "command": {
                "mode": "meeting_status",
                "state": "starting_soon",
                "minutes": 15,
            },
            "seconds_until_expected_state_change": 450,
        }
    )

    assert command == {
        "mode": "meeting_status",
        "state": "starting_soon",
        "minutes": 15,
        "seconds_until_expected_state_change": 450,
    }
