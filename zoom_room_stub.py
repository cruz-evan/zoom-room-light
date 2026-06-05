#!/usr/bin/env python3
"""Run and control a local Zoom room-light stub server."""

from __future__ import annotations

import argparse
import json
import os
import socket
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest


DEFAULT_SERVER_URL = "http://localhost:5050"
DEFAULT_STUB_FILE = ".zoom-room-light.stub-schedule.json"


def main() -> int:
    args = parse_args()
    return args.func(args)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Test the Zoom room light over Wi-Fi with fake room state and schedule."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="start the local Wi-Fi test server")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=5050)
    serve.add_argument("--stub-file", default=DEFAULT_STUB_FILE)
    serve.add_argument("--schedule-poll", type=int, default=2)
    serve.add_argument("--device-poll", type=int, default=5)
    serve.add_argument("--serial", action="store_true", help="also publish over USB serial")
    serve.set_defaults(func=cmd_serve)

    control_parent = argparse.ArgumentParser(add_help=False)
    control_parent.add_argument("--server", default=os.getenv("ZOOM_ROOM_STUB_URL", DEFAULT_SERVER_URL))

    status = subparsers.add_parser(
        "status",
        parents=[control_parent],
        help="set the fake Zoom room status",
    )
    status.add_argument(
        "state",
        choices=[
            "starting-soon",
            "upcoming",
            "in-progress",
            "busy",
            "ending-soon",
            "free",
            "ended",
            "reset",
        ],
    )
    status.add_argument("--meeting-id")
    status.add_argument("--topic", default="Stub meeting")
    status.add_argument("--starts-in", type=float, default=3.0)
    status.add_argument("--ends-in", type=float, default=3.0)
    status.add_argument("--duration", type=int, default=10)
    status.set_defaults(func=cmd_status)

    schedule = subparsers.add_parser(
        "schedule",
        parents=[control_parent],
        help="set or inspect the fake Zoom schedule",
    )
    schedule.add_argument("action", choices=["list", "clear", "upcoming", "ending-soon"])
    schedule.add_argument("--meeting-id")
    schedule.add_argument("--topic", default="Stub meeting")
    schedule.add_argument("--starts-in", type=float, default=3.0)
    schedule.add_argument("--ends-in", type=float, default=3.0)
    schedule.add_argument("--duration", type=int, default=10)
    schedule.set_defaults(func=cmd_schedule)

    scenario = subparsers.add_parser(
        "scenario",
        parents=[control_parent],
        help="play a short starting/in-progress/ending/free sequence",
    )
    scenario.add_argument("--meeting-id", default="stub-meeting")
    scenario.add_argument("--topic", default="Stub meeting")
    scenario.add_argument("--step-seconds", type=float, default=8.0)
    scenario.add_argument("--starts-in", type=float, default=3.0)
    scenario.add_argument("--ends-in", type=float, default=3.0)
    scenario.add_argument("--duration", type=int, default=10)
    scenario.set_defaults(func=cmd_scenario)

    return parser.parse_args()


def cmd_serve(args: argparse.Namespace) -> int:
    stub_path = Path(args.stub_file).resolve()
    os.environ["ZOOM_SCHEDULE_SOURCE"] = "stub"
    os.environ["ZOOM_STUB_SCHEDULE_FILE"] = str(stub_path)
    os.environ["ZOOM_TEST_CONTROL_ENABLED"] = "true"
    os.environ["SCHEDULE_POLL_SECONDS"] = str(args.schedule_poll)
    os.environ["DEVICE_POLL_SECONDS"] = str(args.device_poll)
    if not args.serial:
        os.environ["RP2_SERIAL_ENABLED"] = "false"

    from zoom_light.config import load_config
    from zoom_light.server import ZoomLightServer

    config = load_config(host=args.host, port=args.port)
    server = ZoomLightServer((config.host, config.port), config)
    lan_ip = guess_lan_ip()
    state_host = lan_ip if config.host in ("0.0.0.0", "::", "") else config.host

    print(f"Zoom room stub listening on http://{config.host}:{config.port}", flush=True)
    print(f"Dashboard: http://localhost:{config.port}/", flush=True)
    print(f"Pico STATE_URL: http://{state_host}:{config.port}/device/state", flush=True)
    print(f"Stub schedule file: {stub_path}", flush=True)
    print("Control examples:", flush=True)
    print(f"  python3 zoom_room_stub.py status in-progress --server http://localhost:{config.port}", flush=True)
    print(f"  python3 zoom_room_stub.py schedule upcoming --starts-in 3 --server http://localhost:{config.port}", flush=True)
    server.light.print_state(server.light.snapshot())
    server.light.publish_current_state()
    server.schedule.start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping stub server.", flush=True)
        return 0
    finally:
        server.server_close()


def cmd_status(args: argparse.Namespace) -> int:
    state = args.state
    meeting_id = args.meeting_id or "stub-meeting"

    if state in ("starting-soon", "upcoming"):
        response = start_starting_soon_status(
            args.server,
            meeting_id,
            args.topic,
            args.starts_in,
            args.duration,
        )
        print_result(response)
        return 0

    if state == "ending-soon":
        response = start_ending_soon_status(
            args.server,
            meeting_id,
            args.topic,
            args.ends_in,
            args.duration,
        )
        print_result(response)
        return 0

    if state in ("in-progress", "busy"):
        response = start_active_status(args.server, meeting_id, args.topic, args.duration)
        print_result(response)
        return 0

    if state in ("free", "ended"):
        response = set_free_status(args.server)
        print_result(response)
        return 0

    response = reset_room_status(args.server)
    print_result(response)
    return 0


def cmd_schedule(args: argparse.Namespace) -> int:
    if args.action == "list":
        response = request_json(args.server, "GET", "/test/schedule")
        print_meetings(response.get("meetings", []))
        return 0

    if args.action == "clear":
        response = post_schedule(args.server, [])
        print_result(response)
        return 0

    meeting_id = args.meeting_id or "stub-meeting"
    if args.action == "upcoming":
        meeting = upcoming_meeting(meeting_id, args.topic, args.starts_in, args.duration)
    else:
        meeting = ending_soon_meeting(meeting_id, args.topic, args.ends_in, args.duration)
    response = post_schedule(args.server, [meeting])
    print_result(response)
    return 0


def cmd_scenario(args: argparse.Namespace) -> int:
    steps = [
        (
            "starting soon",
            lambda: start_starting_soon_status(
                args.server,
                args.meeting_id,
                args.topic,
                args.starts_in,
                args.duration,
            ),
        ),
        (
            "in progress",
            lambda: start_active_status(args.server, args.meeting_id, args.topic, args.duration),
        ),
        (
            "ending soon",
            lambda: post_schedule(
                args.server,
                [ending_soon_meeting(args.meeting_id, args.topic, args.ends_in, args.duration)],
            ),
        ),
        ("free", lambda: post_zoom_event(args.server, "meeting.ended", "", "")),
    ]

    for index, (label, action) in enumerate(steps, start=1):
        print(f"[{index}/{len(steps)}] {label}")
        print_result(action())
        if index != len(steps) and args.step_seconds > 0:
            time.sleep(args.step_seconds)
    return 0


def post_schedule(
    server_url: str,
    meetings: list[dict[str, Any]],
    *,
    refresh_schedule: bool = True,
) -> dict[str, Any]:
    return post_json(
        server_url,
        "/test/schedule",
        {"meetings": meetings, "refresh_schedule": refresh_schedule},
    )


def start_starting_soon_status(
    server_url: str,
    meeting_id: str,
    topic: str,
    starts_in_minutes: float,
    duration_minutes: int,
) -> dict[str, Any]:
    reset_room_status(server_url, refresh_schedule=False)
    return post_schedule(
        server_url,
        [upcoming_meeting(meeting_id, topic, starts_in_minutes, duration_minutes)],
    )


def start_active_status(
    server_url: str,
    meeting_id: str,
    topic: str,
    duration_minutes: int,
) -> dict[str, Any]:
    post_schedule(
        server_url,
        [active_meeting(meeting_id, topic, duration_minutes)],
        refresh_schedule=False,
    )
    return post_zoom_event_with_refresh(
        server_url,
        "meeting.started",
        meeting_id,
        topic,
        refresh_schedule=False,
    )


def start_ending_soon_status(
    server_url: str,
    meeting_id: str,
    topic: str,
    ends_in_minutes: float,
    duration_minutes: int,
) -> dict[str, Any]:
    post_schedule(
        server_url,
        [ending_soon_meeting(meeting_id, topic, ends_in_minutes, duration_minutes)],
        refresh_schedule=False,
    )
    return post_zoom_event_with_refresh(
        server_url,
        "meeting.started",
        meeting_id,
        topic,
        refresh_schedule=True,
    )


def set_free_status(server_url: str) -> dict[str, Any]:
    post_schedule(server_url, [], refresh_schedule=False)
    return post_zoom_event_with_refresh(
        server_url,
        "meeting.ended",
        "",
        "",
        refresh_schedule=False,
    )


def reset_room_status(server_url: str, *, refresh_schedule: bool = False) -> dict[str, Any]:
    return post_json(
        server_url,
        "/test/zoom-event",
        {
            "event": "manual.reset",
            "payload": {"object": {}},
            "refresh_schedule": refresh_schedule,
        },
    )


def post_zoom_event(server_url: str, event: str, meeting_id: str, topic: str) -> dict[str, Any]:
    return post_zoom_event_with_refresh(
        server_url,
        event,
        meeting_id,
        topic,
        refresh_schedule=True,
    )


def post_zoom_event_with_refresh(
    server_url: str,
    event: str,
    meeting_id: str,
    topic: str,
    *,
    refresh_schedule: bool,
) -> dict[str, Any]:
    meeting = {"id": meeting_id, "uuid": meeting_id, "topic": topic} if meeting_id else {}
    return post_json(
        server_url,
        "/test/zoom-event",
        {
            "event": event,
            "payload": {"object": meeting},
            "refresh_schedule": refresh_schedule,
        },
    )


def post_json(server_url: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
    return request_json(server_url, "POST", path, body)


def request_json(
    server_url: str,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    url = f"{server_url.rstrip('/')}{path}"
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urlrequest.Request(url, data=data, headers=headers, method=method)
    try:
        with urlrequest.urlopen(req, timeout=8) as response:
            raw = response.read().decode("utf-8")
    except urlerror.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        raise SystemExit(f"{method} {url} failed: HTTP {exc.code}: {raw}") from exc
    except urlerror.URLError as exc:
        raise SystemExit(f"Could not reach stub server at {server_url}: {exc.reason}") from exc

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{method} {url} returned non-JSON: {raw[:300]}") from exc


def upcoming_meeting(
    meeting_id: str,
    topic: str,
    starts_in_minutes: float,
    duration_minutes: int,
) -> dict[str, Any]:
    start_time = datetime.now(timezone.utc) + timedelta(minutes=starts_in_minutes)
    return meeting_payload(meeting_id, topic, start_time, duration_minutes)


def ending_soon_meeting(
    meeting_id: str,
    topic: str,
    ends_in_minutes: float,
    duration_minutes: int,
) -> dict[str, Any]:
    duration = max(duration_minutes, int(ends_in_minutes) + 1)
    start_time = datetime.now(timezone.utc) + timedelta(minutes=ends_in_minutes - duration)
    return meeting_payload(meeting_id, topic, start_time, duration)


def active_meeting(
    meeting_id: str,
    topic: str,
    duration_minutes: int,
) -> dict[str, Any]:
    duration = max(duration_minutes, 10)
    start_time = datetime.now(timezone.utc) - timedelta(minutes=1)
    return meeting_payload(meeting_id, topic, start_time, duration)


def meeting_payload(
    meeting_id: str,
    topic: str,
    start_time: datetime,
    duration_minutes: int,
) -> dict[str, Any]:
    return {
        "id": meeting_id,
        "uuid": meeting_id,
        "topic": topic,
        "start_time": utc_iso(start_time),
        "duration": duration_minutes,
        "timezone": "UTC",
        "type": "scheduled",
    }


def utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def print_result(response: dict[str, Any]) -> None:
    state = response.get("state") if isinstance(response.get("state"), dict) else response
    print(state_summary(state))


def state_summary(state: dict[str, Any]) -> str:
    if not isinstance(state, dict):
        return json.dumps(state, indent=2)

    parts = [
        f"LIGHT={str(state.get('color', '')).upper()}",
        f"STATUS={state.get('label', '')}",
        f"EVENT={state.get('last_event', '')}",
    ]
    if state.get("active_meeting_id"):
        parts.append(f"active={state.get('active_meeting_id')}")
    if state.get("next_meeting_id"):
        parts.append(f"next={state.get('next_meeting_id')} in {state.get('minutes_until_next')}m")
    if state.get("minutes_until_end") is not None:
        parts.append(f"ending_in={state.get('minutes_until_end')}m")
    return " ".join(parts)


def print_meetings(meetings: list[Any]) -> None:
    if not meetings:
        print("No stub meetings scheduled.")
        return

    for meeting in meetings:
        if not isinstance(meeting, dict):
            print(meeting)
            continue
        print(
            f"{meeting.get('id', '-')} | {meeting.get('start_time', '-')} | "
            f"{meeting.get('duration', '-')}m | {meeting.get('topic', '')}"
        )


def guess_lan_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "YOUR_LAPTOP_LAN_IP"
    finally:
        sock.close()


if __name__ == "__main__":
    raise SystemExit(main())
