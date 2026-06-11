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


def test_ota_config_command_builds_gh_workflow_run():
    command = zoom_room_stub.build_ota_config_command(
        argparse.Namespace(workflow="pico-ota.yml", ref="main", repo="cruz-evan/zoom-room-light")
    )

    assert command == [
        "gh",
        "workflow",
        "run",
        "pico-ota.yml",
        "--ref",
        "main",
        "--repo",
        "cruz-evan/zoom-room-light",
    ]


def test_ota_config_command_omits_repo_when_unset():
    command = zoom_room_stub.build_ota_config_command(
        argparse.Namespace(workflow="pico-ota.yml", ref="main", repo="")
    )

    assert command == ["gh", "workflow", "run", "pico-ota.yml", "--ref", "main"]


def test_ota_config_dry_run_prints_command(capsys):
    result = zoom_room_stub.cmd_ota_config(
        argparse.Namespace(
            workflow="pico-ota.yml",
            ref="main",
            repo="cruz-evan/zoom-room-light",
            dry_run=True,
        )
    )

    assert result == 0
    assert (
        "gh workflow run pico-ota.yml --ref main --repo cruz-evan/zoom-room-light"
        in capsys.readouterr().out
    )


def test_ota_config_runs_gh_workflow(monkeypatch, capsys):
    calls = []

    def fake_run(command, check):
        calls.append((command, check))

    monkeypatch.setattr(zoom_room_stub.subprocess, "run", fake_run)

    result = zoom_room_stub.cmd_ota_config(
        argparse.Namespace(
            workflow="pico-ota.yml",
            ref="main",
            repo="cruz-evan/zoom-room-light",
            dry_run=False,
        )
    )

    assert result == 0
    assert calls == [
        (
            [
                "gh",
                "workflow",
                "run",
                "pico-ota.yml",
                "--ref",
                "main",
                "--repo",
                "cruz-evan/zoom-room-light",
            ],
            True,
        )
    ]
    assert "Workflow dispatched" in capsys.readouterr().out


def test_ota_force_posts_to_simulate_ota(monkeypatch, capsys):
    calls = []

    def fake_request_json(server_url, method, path, body=None, *, token=None):
        calls.append((server_url, method, path, body, token))
        return {
            "state": {
                "command": {"mode": "off"},
                "last_event": "simulate.ota.requested",
                "ota_check_requested_at": "2026-06-11T22:30:00Z",
            }
        }

    monkeypatch.setattr(zoom_room_stub, "request_json", fake_request_json)

    result = zoom_room_stub.cmd_ota_force(
        argparse.Namespace(
            server="https://relay.test",
            admin_token="admin-token",
            dry_run=False,
        )
    )

    assert result == 0
    assert calls == [
        (
            "https://relay.test",
            "POST",
            "/simulate/ota",
            None,
            "admin-token",
        )
    ]
    output = capsys.readouterr().out
    assert "EVENT=simulate.ota.requested" in output
    assert "OTA_REQUESTED=2026-06-11T22:30:00Z" in output


def test_ota_force_dry_run_prints_request(capsys):
    result = zoom_room_stub.cmd_ota_force(
        argparse.Namespace(
            server="https://relay.test/",
            admin_token="admin-token",
            dry_run=True,
        )
    )

    assert result == 0
    assert "POST https://relay.test/simulate/ota" in capsys.readouterr().out
