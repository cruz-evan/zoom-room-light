from device.state_client import state_url_for_device


def test_state_url_appends_device_id_to_single_device_url():
    assert (
        state_url_for_device("https://relay.test/device/state", "board-room-a")
        == "https://relay.test/device/state?device_id=board-room-a"
    )


def test_state_url_preserves_existing_query_params():
    assert (
        state_url_for_device("https://relay.test/device/state?source=pico", "board-room-a")
        == "https://relay.test/device/state?source=pico&device_id=board-room-a"
    )


def test_state_url_allows_explicit_template():
    assert (
        state_url_for_device("https://relay.test/device/{device_id}/state", "board-room-a")
        == "https://relay.test/device/board-room-a/state"
    )


def test_state_url_does_not_duplicate_device_id():
    url = "https://relay.test/device/state?device_id=board-room-a"

    assert state_url_for_device(url, "board-room-b") == url
