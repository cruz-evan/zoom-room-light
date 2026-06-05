#!/usr/bin/env python3
"""Compatibility commands for driving the Worker simulation endpoints."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest


REPO_ROOT = Path(__file__).resolve().parent
WORKER_DIR = REPO_ROOT / "cloudflare-worker"
DEFAULT_SERVER_URL = "http://localhost:5050"
DEFAULT_STUB_FILE = ".zoom-room-light.stub-schedule.json"


def main() -> int:
    args = parse_args()
    return args.func(args)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Drive fake Zoom room states through the Worker simulation API."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser(
        "serve",
        help="start a local Worker stub server for Pico Wi-Fi polling",
    )
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=5050)
    serve.add_argument("--stub-file", default=DEFAULT_STUB_FILE, help=argparse.SUPPRESS)
    serve.add_argument("--schedule-poll", type=int, default=2, help=argparse.SUPPRESS)
    serve.add_argument("--device-poll", type=int, default=5)
    serve.add_argument("--serial", action="store_true", help=argparse.SUPPRESS)
    serve.set_defaults(func=cmd_serve)

    control_parent = argparse.ArgumentParser(add_help=False)
    control_parent.add_argument("--server", default=default_server_url())
    control_parent.add_argument("--admin-token", default=default_admin_token())
    control_parent.add_argument("--device-token", default=default_device_token())

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
    status.add_argument("--meeting-id", help=argparse.SUPPRESS)
    status.add_argument("--topic", default="Stub meeting", help=argparse.SUPPRESS)
    status.add_argument("--starts-in", type=float, default=3.0)
    status.add_argument("--ends-in", type=float, default=3.0)
    status.add_argument("--duration", type=int, default=10, help=argparse.SUPPRESS)
    status.set_defaults(func=cmd_status)

    schedule = subparsers.add_parser(
        "schedule",
        parents=[control_parent],
        help="set or inspect the fake Zoom schedule state",
    )
    schedule.add_argument("action", choices=["list", "clear", "upcoming", "ending-soon"])
    schedule.add_argument("--meeting-id", help=argparse.SUPPRESS)
    schedule.add_argument("--topic", default="Stub meeting", help=argparse.SUPPRESS)
    schedule.add_argument("--starts-in", type=float, default=3.0)
    schedule.add_argument("--ends-in", type=float, default=3.0)
    schedule.add_argument("--duration", type=int, default=10, help=argparse.SUPPRESS)
    schedule.set_defaults(func=cmd_schedule)

    scenario = subparsers.add_parser(
        "scenario",
        parents=[control_parent],
        help="play a short starting/in-progress/ending/free sequence",
    )
    scenario.add_argument("--meeting-id", default="stub-meeting", help=argparse.SUPPRESS)
    scenario.add_argument("--topic", default="Stub meeting", help=argparse.SUPPRESS)
    scenario.add_argument("--step-seconds", type=float, default=8.0)
    scenario.add_argument("--starts-in", type=float, default=3.0)
    scenario.add_argument("--ends-in", type=float, default=3.0)
    scenario.add_argument("--duration", type=int, default=10, help=argparse.SUPPRESS)
    scenario.set_defaults(func=cmd_scenario)

    return parser.parse_args()


def cmd_serve(args: argparse.Namespace) -> int:
    if not (WORKER_DIR / "package.json").exists():
        raise SystemExit(f"Could not find Worker project at {WORKER_DIR}")

    lan_ip = guess_lan_ip()
    state_host = lan_ip if args.host in ("0.0.0.0", "::", "") else args.host
    server_url = f"http://localhost:{args.port}"
    env = os.environ.copy()
    env["POLL_SECONDS"] = str(args.device_poll)

    print("Starting local Worker stub server.", flush=True)
    print(f"Worker URL: {server_url}", flush=True)
    print(f"Pico STATE_URL: http://{state_host}:{args.port}/device/state", flush=True)
    print("Control examples:", flush=True)
    print(f"  python3 zoom_room_stub.py status starting-soon --starts-in 3 --server {server_url}", flush=True)
    print(f"  python3 zoom_room_stub.py status in-progress --server {server_url}", flush=True)
    print(f"  python3 zoom_room_stub.py status ending-soon --ends-in 3 --server {server_url}", flush=True)
    print(f"  python3 zoom_room_stub.py status free --server {server_url}", flush=True)

    command = ["npm", "run", "dev", "--", "--ip", args.host, "--port", str(args.port)]
    return subprocess.call(command, cwd=WORKER_DIR, env=env)


def cmd_status(args: argparse.Namespace) -> int:
    require_admin_token(args)
    state = args.state

    if state in ("starting-soon", "upcoming"):
        response = start_starting_soon_status(
            args.server,
            args.meeting_id or "stub-meeting",
            args.topic,
            args.starts_in,
            args.duration,
            admin_token=args.admin_token,
        )
    elif state in ("in-progress", "busy"):
        response = start_active_status(
            args.server,
            args.meeting_id or "stub-meeting",
            args.topic,
            args.duration,
            admin_token=args.admin_token,
        )
    elif state == "ending-soon":
        response = start_ending_soon_status(
            args.server,
            args.meeting_id or "stub-meeting",
            args.topic,
            args.ends_in,
            args.duration,
            admin_token=args.admin_token,
        )
    elif state in ("free", "ended"):
        response = set_free_status(args.server, admin_token=args.admin_token)
    else:
        response = reset_room_status(args.server, admin_token=args.admin_token)

    print_result(response)
    return 0


def cmd_schedule(args: argparse.Namespace) -> int:
    if args.action == "list":
        response = read_device_state(args.server, device_token=args.device_token)
        print_result(response)
        return 0

    require_admin_token(args)

    if args.action == "clear":
        response = reset_room_status(args.server, admin_token=args.admin_token)
    elif args.action == "upcoming":
        response = start_starting_soon_status(
            args.server,
            args.meeting_id or "stub-meeting",
            args.topic,
            args.starts_in,
            args.duration,
            admin_token=args.admin_token,
        )
    else:
        response = start_ending_soon_status(
            args.server,
            args.meeting_id or "stub-meeting",
            args.topic,
            args.ends_in,
            args.duration,
            admin_token=args.admin_token,
        )

    print_result(response)
    return 0


def cmd_scenario(args: argparse.Namespace) -> int:
    require_admin_token(args)

    steps = [
        (
            "starting soon",
            lambda: start_starting_soon_status(
                args.server,
                args.meeting_id,
                args.topic,
                args.starts_in,
                args.duration,
                admin_token=args.admin_token,
            ),
        ),
        (
            "in progress",
            lambda: start_active_status(
                args.server,
                args.meeting_id,
                args.topic,
                args.duration,
                admin_token=args.admin_token,
            ),
        ),
        (
            "ending soon",
            lambda: start_ending_soon_status(
                args.server,
                args.meeting_id,
                args.topic,
                args.ends_in,
                args.duration,
                admin_token=args.admin_token,
            ),
        ),
        ("free", lambda: set_free_status(args.server, admin_token=args.admin_token)),
    ]

    for index, (label, action) in enumerate(steps, start=1):
        print(f"[{index}/{len(steps)}] {label}")
        print_result(action())
        if index != len(steps) and args.step_seconds > 0:
            time.sleep(args.step_seconds)
    return 0


def start_starting_soon_status(
    server_url: str,
    meeting_id: str,
    topic: str,
    starts_in_minutes: float,
    duration_minutes: int,
    *,
    admin_token: str | None = None,
) -> dict[str, Any]:
    del meeting_id, topic, duration_minutes
    return post_simulate(server_url, "starting-soon", starts_in_minutes, admin_token=admin_token)


def start_active_status(
    server_url: str,
    meeting_id: str,
    topic: str,
    duration_minutes: int,
    *,
    admin_token: str | None = None,
) -> dict[str, Any]:
    del meeting_id, topic, duration_minutes
    return post_simulate(server_url, "start", admin_token=admin_token)


def start_ending_soon_status(
    server_url: str,
    meeting_id: str,
    topic: str,
    ends_in_minutes: float,
    duration_minutes: int,
    *,
    admin_token: str | None = None,
) -> dict[str, Any]:
    del meeting_id, topic, duration_minutes
    return post_simulate(server_url, "ending-soon", ends_in_minutes, admin_token=admin_token)


def set_free_status(server_url: str, *, admin_token: str | None = None) -> dict[str, Any]:
    return post_simulate(server_url, "end", admin_token=admin_token)


def reset_room_status(server_url: str, *, admin_token: str | None = None) -> dict[str, Any]:
    return post_simulate(server_url, "reset", admin_token=admin_token)


def read_device_state(server_url: str, *, device_token: str | None = None) -> dict[str, Any]:
    return request_json(server_url, "GET", "/device/state", token=device_token)


def post_simulate(
    server_url: str,
    action: str,
    minutes: float | None = None,
    *,
    admin_token: str | None = None,
) -> dict[str, Any]:
    path = f"/simulate/{action}"
    if minutes is not None:
        path = f"{path}?{urlparse.urlencode({'minutes': format_minutes(minutes)})}"
    return request_json(server_url, "POST", path, token=admin_token)


def format_minutes(minutes: float) -> str:
    value = float(minutes)
    if value.is_integer():
        return str(int(value))
    return str(value)


def request_json(
    server_url: str,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    token: str | None = None,
) -> dict[str, Any]:
    url = f"{server_url.rstrip('/')}{path}"
    data = None
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urlrequest.Request(url, data=data, headers=headers, method=method)
    try:
        with urlrequest.urlopen(req, timeout=8) as response:
            raw = response.read().decode("utf-8")
    except urlerror.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        raise SystemExit(http_error_message(method, url, exc.code, raw)) from exc
    except urlerror.URLError as exc:
        raise SystemExit(f"Could not reach stub server at {server_url}: {exc.reason}") from exc

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{method} {url} returned non-JSON: {raw[:300]}") from exc


def http_error_message(method: str, url: str, code: int, raw: str) -> str:
    hint = ""
    if code == 401:
        hint = " Check ADMIN_TOKEN or pass --admin-token."
    elif code == 404 and "simulate_disabled" in raw:
        hint = " Set ADMIN_TOKEN for the Worker to enable simulation routes."
    return f"{method} {url} failed: HTTP {code}: {raw}{hint}"


def require_admin_token(args: argparse.Namespace) -> None:
    if args.admin_token:
        return
    raise SystemExit(
        "ADMIN_TOKEN is required for simulation commands. Set ADMIN_TOKEN, "
        "ZOOM_ROOM_ADMIN_TOKEN, cloudflare-worker/.dev.vars, or pass --admin-token."
    )


def print_result(response: dict[str, Any]) -> None:
    state = response.get("state") if isinstance(response.get("state"), dict) else response
    print(state_summary(state))


def state_summary(state: dict[str, Any]) -> str:
    if not isinstance(state, dict):
        return json.dumps(state, indent=2)

    command = state.get("command")
    parts = [f"COMMAND={command_summary(command)}"]
    if state.get("last_event"):
        parts.append(f"EVENT={state.get('last_event')}")
    if state.get("updated_at"):
        parts.append(f"UPDATED={state.get('updated_at')}")
    if state.get("poll_seconds") is not None:
        parts.append(f"POLL={state.get('poll_seconds')}s")
    return " ".join(parts)


def command_summary(command: Any) -> str:
    if not isinstance(command, dict):
        return json.dumps(command, separators=(",", ":"))

    mode = command.get("mode")
    if mode == "off":
        return "off"

    if mode == "meeting_status":
        state = str(command.get("state", "")).replace("_", "-")
        if command.get("minutes") is not None:
            return f"{state} minutes={command.get('minutes')}"
        return state

    return json.dumps(command, separators=(",", ":"))


def default_server_url() -> str:
    for key in ("ZOOM_ROOM_STUB_URL", "ZOOM_ROOM_RELAY_URL", "RELAY"):
        value = os.getenv(key)
        if value:
            return value
    return DEFAULT_SERVER_URL


def default_admin_token() -> str:
    return default_secret("ZOOM_ROOM_ADMIN_TOKEN", "ADMIN_TOKEN")


def default_device_token() -> str:
    return default_secret("ZOOM_ROOM_DEVICE_TOKEN", "DEVICE_TOKEN")


def default_secret(*keys: str) -> str:
    for key in keys:
        value = os.getenv(key)
        if value:
            return value

    env_values = local_env_values()
    for key in keys:
        value = env_values.get(key)
        if value:
            return value
    return ""


def local_env_values() -> dict[str, str]:
    values: dict[str, str] = {}
    for path in (REPO_ROOT / ".env", WORKER_DIR / ".dev.vars"):
        values.update(read_env_file(path))
    return values


def read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key:
            values[key] = value
    return values


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
