import argparse

import zoom_room_stub


def test_starting_soon_status_posts_to_worker_simulate_endpoint(monkeypatch):
    calls = []

    def fake_request_json(server_url, method, path, body=None, *, token=None):
        calls.append((server_url, method, path, body, token))
        return {"state": {"command": {"mode": "meeting_status", "state": "starting_soon"}}}

    monkeypatch.setattr(zoom_room_stub, "request_json", fake_request_json)

    response = zoom_room_stub.start_starting_soon_status(
        "http://localhost:5050",
        "demo-meeting",
        "Demo",
        3,
        10,
        admin_token="admin-token",
    )

    assert response == {"state": {"command": {"mode": "meeting_status", "state": "starting_soon"}}}
    assert calls == [
        (
            "http://localhost:5050",
            "POST",
            "/simulate/starting-soon?minutes=3",
            None,
            "admin-token",
        )
    ]


def test_status_aliases_map_to_simulate_actions(monkeypatch, capsys):
    calls = []

    def fake_request_json(server_url, method, path, body=None, *, token=None):
        calls.append(path)
        return {"state": {"command": {"mode": "off"}, "last_event": "simulate.reset"}}

    monkeypatch.setattr(zoom_room_stub, "request_json", fake_request_json)

    for state in ("busy", "ended", "reset"):
        result = zoom_room_stub.cmd_status(
            argparse.Namespace(
                state=state,
                server="http://localhost:5050",
                admin_token="admin-token",
                meeting_id=None,
                topic="Demo",
                starts_in=3,
                ends_in=2,
                duration=10,
            )
        )
        assert result == 0

    assert calls == ["/simulate/start", "/simulate/end", "/simulate/reset"]
    assert "COMMAND=off" in capsys.readouterr().out


def test_schedule_ending_soon_matches_status_ending_soon(monkeypatch):
    calls = []

    def fake_request_json(server_url, method, path, body=None, *, token=None):
        calls.append((server_url, method, path, body, token))
        return {"state": {"command": {"mode": "meeting_status", "state": "ending_soon"}}}

    monkeypatch.setattr(zoom_room_stub, "request_json", fake_request_json)

    result = zoom_room_stub.cmd_schedule(
        argparse.Namespace(
            action="ending-soon",
            server="http://localhost:5050",
            admin_token="admin-token",
            device_token="device-token",
            meeting_id=None,
            topic="Demo",
            starts_in=3,
            ends_in=2,
            duration=10,
        )
    )

    assert result == 0
    assert calls == [
        (
            "http://localhost:5050",
            "POST",
            "/simulate/ending-soon?minutes=2",
            None,
            "admin-token",
        )
    ]


def test_schedule_list_reads_device_state(monkeypatch, capsys):
    calls = []

    def fake_request_json(server_url, method, path, body=None, *, token=None):
        calls.append((server_url, method, path, token))
        return {
            "command": {"mode": "meeting_status", "state": "in_progress"},
            "last_event": "simulate.meeting.started",
        }

    monkeypatch.setattr(zoom_room_stub, "request_json", fake_request_json)

    result = zoom_room_stub.cmd_schedule(
        argparse.Namespace(
            action="list",
            server="http://localhost:5050",
            admin_token="",
            device_token="device-token",
            meeting_id=None,
            topic="Demo",
            starts_in=3,
            ends_in=2,
            duration=10,
        )
    )

    assert result == 0
    assert calls == [("http://localhost:5050", "GET", "/device/state", "device-token")]
    assert "COMMAND=in-progress" in capsys.readouterr().out


def test_format_minutes_keeps_cli_urls_tidy():
    assert zoom_room_stub.format_minutes(3.0) == "3"
    assert zoom_room_stub.format_minutes(2.5) == "2.5"
