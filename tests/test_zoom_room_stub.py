import zoom_room_stub


def test_starting_soon_status_clears_active_meeting_before_schedule(monkeypatch):
    calls = []

    def fake_post_json(server_url, path, body):
        calls.append((server_url, path, body))
        if path == "/test/schedule":
            return {"state": {"color": "yellow", "label": "STARTS SOON"}}
        return {"state": {"color": "green", "label": "FREE"}}

    monkeypatch.setattr(zoom_room_stub, "post_json", fake_post_json)

    response = zoom_room_stub.start_starting_soon_status(
        "http://localhost:5050",
        "demo-meeting",
        "Demo",
        3,
        10,
    )

    assert response == {"state": {"color": "yellow", "label": "STARTS SOON"}}
    assert calls[0] == (
        "http://localhost:5050",
        "/test/zoom-event",
        {
            "event": "manual.reset",
            "payload": {"object": {}},
            "refresh_schedule": False,
        },
    )
    assert calls[1][1] == "/test/schedule"
    assert calls[1][2]["refresh_schedule"] is True
    assert calls[1][2]["meetings"][0]["id"] == "demo-meeting"


def test_free_status_clears_schedule_before_ending_meeting(monkeypatch):
    calls = []

    def fake_post_json(server_url, path, body):
        calls.append((path, body))
        return {"state": {"color": "green", "label": "FREE"}}

    monkeypatch.setattr(zoom_room_stub, "post_json", fake_post_json)

    response = zoom_room_stub.set_free_status("http://localhost:5050")

    assert response == {"state": {"color": "green", "label": "FREE"}}
    assert calls == [
        ("/test/schedule", {"meetings": [], "refresh_schedule": False}),
        (
            "/test/zoom-event",
            {
                "event": "meeting.ended",
                "payload": {"object": {}},
                "refresh_schedule": False,
            },
        ),
    ]
