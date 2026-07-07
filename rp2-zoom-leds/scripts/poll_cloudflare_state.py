#!/usr/bin/env python3
import argparse
import json
import os
import shlex
import ssl
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PICO_ROOT = REPO_ROOT / "rp2-zoom-leds"
DEFAULT_RELAY_URL = "https://zoom-led-room-light.connor-zoom-led-room-light.workers.dev"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Poll the Cloudflare Worker state endpoint for each Pico device."
    )
    parser.add_argument(
        "--relay",
        default=default_relay_url(),
        help="Worker base URL or /device/state URL. Defaults to ZOOM_ROOM_RELAY_URL, RELAY, STATE_URL, then production.",
    )
    parser.add_argument(
        "--token",
        default=default_state_poll_token(),
        help="State polling bearer token. Defaults to STATE_POLL_TOKEN, ZOOM_ROOM_DEVICE_TOKEN, DEVICE_POLL_TOKEN, or DEVICE_TOKEN.",
    )
    parser.add_argument(
        "--source",
        choices=("auto", "endpoint", "kv"),
        default="auto",
        help="Read via the device endpoint, remote Workers KV, or auto. Auto uses endpoint when a token is available; otherwise KV.",
    )
    parser.add_argument("--kv-binding", default="STATE_KV")
    parser.add_argument("--wrangler-command", default="npx wrangler")
    parser.add_argument(
        "--devices-file",
        type=Path,
        default=None,
        help="JSON file with a top-level devices object. Defaults to rp2-zoom-leds/devices.json, PICO_ROOM_ASSIGNMENTS, then devices.example.json.",
    )
    parser.add_argument(
        "--device",
        action="append",
        dest="devices",
        default=[],
        help="Device ID to poll. May be passed more than once. When omitted, IDs are read from --devices-file or PICO_ROOM_ASSIGNMENTS.",
    )
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--json", action="store_true", help="Print full JSON results.")
    return parser.parse_args()


def default_relay_url():
    values = env_values()
    for key in ("ZOOM_ROOM_RELAY_URL", "RELAY", "STATE_URL"):
        value = values.get(key, "")
        if value:
            return value
    return DEFAULT_RELAY_URL


def default_state_poll_token():
    values = env_values()
    for key in ("STATE_POLL_TOKEN", "ZOOM_ROOM_DEVICE_TOKEN", "DEVICE_POLL_TOKEN", "DEVICE_TOKEN"):
        value = values.get(key, "")
        if value:
            return value
    return ""


def env_values():
    values = {}
    values.update(read_env_file(REPO_ROOT / ".env"))
    values.update(read_env_file(REPO_ROOT / "cloudflare-worker" / ".dev.vars"))
    values.update(read_wrangler_vars(REPO_ROOT / "cloudflare-worker" / "wrangler.toml"))
    values.update(os.environ)
    return values


def read_env_file(path):
    if not path.exists():
        return {}

    values = {}
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


def read_wrangler_vars(path):
    if not path.exists():
        return {}

    with path.open("rb") as handle:
        data = tomllib.load(handle)
    vars_section = data.get("vars", {})
    if not isinstance(vars_section, dict):
        return {}
    return {str(key): str(value) for key, value in vars_section.items()}


def configured_devices(args):
    if args.devices:
        return [{"id": device_id, "name": ""} for device_id in args.devices]

    if args.devices_file is not None:
        devices = devices_from_file(args.devices_file)
        if devices:
            return devices
        raise SystemExit(f"No devices found in {args.devices_file}")

    for path in (PICO_ROOT / "devices.json",):
        devices = devices_from_file(path)
        if devices:
            return devices

    devices = devices_from_assignments(env_values().get("PICO_ROOM_ASSIGNMENTS", ""))
    if devices:
        return devices

    devices = devices_from_file(PICO_ROOT / "devices.example.json")
    if devices:
        return devices

    raise SystemExit(
        "No devices found. Pass --device, create rp2-zoom-leds/devices.json, "
        "or set PICO_ROOM_ASSIGNMENTS."
    )


def devices_from_file(path):
    if not path.exists():
        return []

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path} is not valid JSON: {exc}") from exc

    devices = data.get("devices", data)
    if not isinstance(devices, dict):
        raise SystemExit(f"{path} must contain a top-level devices object")

    results = []
    for device_id, details in devices.items():
        if not device_id:
            continue
        name = details.get("name", "") if isinstance(details, dict) else ""
        results.append({"id": str(device_id), "name": str(name)})
    return results


def devices_from_assignments(raw):
    if not raw:
        return []

    try:
        assignments = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"PICO_ROOM_ASSIGNMENTS is not valid JSON: {exc}") from exc

    if not isinstance(assignments, dict):
        raise SystemExit("PICO_ROOM_ASSIGNMENTS must be a JSON object")

    results = []
    for device_id, details in assignments.items():
        if not device_id:
            continue
        name = ""
        if isinstance(details, dict):
            name = details.get("physical_room_name", "") or details.get("name", "")
        results.append({"id": str(device_id), "name": str(name)})
    return results


def state_url_for_device(relay, device_id):
    relay = relay.rstrip("/")
    if "{device_id}" in relay:
        return add_cache_buster(relay.replace("{device_id}", urllib.parse.quote(device_id, safe="")))

    parsed = urllib.parse.urlparse(relay)
    path = parsed.path.rstrip("/")
    if path.endswith("/device/state"):
        return add_cache_buster(add_query_param(relay, "device_id", device_id))

    return add_cache_buster(add_query_param(f"{relay}/device/state", "device_id", device_id))


def add_query_param(url, key, value):
    parsed = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    if not any(name == key for name, _ in params):
        params.append((key, value))
    query = urllib.parse.urlencode(params)
    return urllib.parse.urlunparse(parsed._replace(query=query))


def add_cache_buster(url):
    return add_query_param(url, "_", str(int(time.time() * 1000)))


def poll_device(relay, token, device, timeout):
    url = state_url_for_device(relay, device["id"])
    headers = {
        "Accept": "application/json",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "User-Agent": "zoom-room-light-state-poller/1.0",
        "X-Device-ID": device["id"],
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout,
            context=ssl_context_for_url(url),
        ) as response:
            body = response.read().decode("utf-8")
            return {
                "device_id": device["id"],
                "name": device.get("name", ""),
                "ok": True,
                "status": response.status,
                "url": url,
                "state": json.loads(body),
            }
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        return failed_result(device, url, f"HTTP {exc.code}: {body[:300]}")
    except urllib.error.URLError as exc:
        return failed_result(device, url, str(exc.reason))
    except json.JSONDecodeError as exc:
        return failed_result(device, url, f"non-JSON response: {exc}")


def poll_kv_device(args, device):
    key = f"current-state:{device['id']}"
    command = shlex.split(args.wrangler_command) + [
        "kv",
        "key",
        "get",
        key,
        "--binding",
        args.kv_binding,
        "--remote",
        "--text",
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=REPO_ROOT / "cloudflare-worker",
            check=False,
            capture_output=True,
            text=True,
            timeout=max(args.timeout, 30),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return failed_result(device, key, str(exc))

    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout).strip()
        return failed_result(device, key, message)

    raw = completed.stdout.strip()
    try:
        state = json.loads(raw)
    except json.JSONDecodeError as exc:
        return failed_result(device, key, f"non-JSON KV value: {exc}: {raw[:300]}")

    return {
        "device_id": device["id"],
        "name": device.get("name", ""),
        "ok": True,
        "source": "kv",
        "key": key,
        "state": state,
    }


def ssl_context_for_url(url):
    if urllib.parse.urlparse(url).scheme != "https":
        return None

    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()

    return ssl.create_default_context(cafile=certifi.where())


def failed_result(device, url, error):
    return {
        "device_id": device["id"],
        "name": device.get("name", ""),
        "ok": False,
        "url": url,
        "error": error,
    }


def state_summary(state):
    command = state.get("command") if isinstance(state, dict) else None
    parts = [f"COMMAND={command_summary(command)}"]
    if isinstance(state, dict):
        if state.get("last_event"):
            parts.append(f"EVENT={state.get('last_event')}")
        if state.get("updated_at"):
            parts.append(f"UPDATED={state.get('updated_at')}")
        if state.get("poll_seconds") is not None:
            parts.append(f"POLL={state.get('poll_seconds')}s")
    return " ".join(parts)


def command_summary(command):
    if not isinstance(command, dict):
        return json.dumps(command, separators=(",", ":"))
    if command.get("mode") == "off":
        return "off"
    if command.get("mode") == "meeting_status":
        state = str(command.get("state", "")).replace("_", "-")
        if command.get("minutes") is not None:
            return f"{state} minutes={command.get('minutes')}"
        return state
    return json.dumps(command, separators=(",", ":"))


def print_text_results(results):
    for result in results:
        label = result["device_id"]
        if result.get("name"):
            label = f"{label} ({result['name']})"
        if result["ok"]:
            print(f"{label}: {state_summary(result['state'])}")
        else:
            print(f"{label}: ERROR={result['error']}", file=sys.stderr)


def main():
    args = parse_args()
    devices = configured_devices(args)
    use_kv = args.source == "kv" or (args.source == "auto" and not args.token)
    if use_kv:
        results = [poll_kv_device(args, device) for device in devices]
    else:
        results = [poll_device(args.relay, args.token, device, args.timeout) for device in devices]

    if args.json:
        print(json.dumps(results, indent=2, sort_keys=True))
    else:
        print_text_results(results)

    return 0 if all(result["ok"] for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
