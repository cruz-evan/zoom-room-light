from __future__ import annotations

import argparse
import datetime as dt
import json
import socket
import sys
from pathlib import Path
from typing import Any


DEFAULT_BIND = "0.0.0.0"
DEFAULT_PORT = 9977


def format_event(payload: dict[str, Any], address: tuple[str, int], received_at: dt.datetime) -> str:
    event = str(payload.get("event", "unknown"))
    device = str(payload.get("device", "device"))
    sequence = payload.get("seq", "?")
    source = f"{address[0]}:{address[1]}"

    fields = []
    for key in (
        "changed",
        "ok",
        "elapsed_ms",
        "fetch_ms",
        "failures",
        "state",
        "error",
        "ip",
    ):
        if key in payload:
            value = payload[key]
            if isinstance(value, (dict, list)):
                value = json.dumps(value, separators=(",", ":"))
            fields.append(f"{key}={value}")

    if "command" in payload:
        fields.append("command=" + json.dumps(payload["command"], separators=(",", ":")))

    suffix = " " + " ".join(fields) if fields else ""
    timestamp = received_at.astimezone().isoformat(timespec="seconds")
    return f"{timestamp} {source} {device}#{sequence} {event}{suffix}"


def write_jsonl(handle, payload: dict[str, Any], address: tuple[str, int], received_at: dt.datetime) -> None:
    record = {
        "received_at": received_at.astimezone().isoformat(timespec="milliseconds"),
        "remote_ip": address[0],
        "remote_port": address[1],
        "payload": payload,
    }
    handle.write(json.dumps(record, separators=(",", ":")) + "\n")
    handle.flush()


def listen(args: argparse.Namespace) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((args.bind, args.port))

    output = None
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        output = path.open("a", encoding="utf-8")

    print(f"Listening for Pico telemetry on udp://{args.bind}:{args.port}", file=sys.stderr)
    try:
        while True:
            data, address = sock.recvfrom(args.max_bytes)
            received_at = dt.datetime.now(dt.timezone.utc)
            text = data.decode("utf-8", errors="replace").strip()

            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                print(f"{received_at.astimezone().isoformat(timespec='seconds')} {address[0]}:{address[1]} {text}")
                continue

            if output is not None:
                write_jsonl(output, payload, address, received_at)

            if args.raw:
                print(text)
            else:
                print(format_event(payload, address, received_at))
    finally:
        if output is not None:
            output.close()
        sock.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Listen for Pico W UDP telemetry logs.")
    parser.add_argument("--bind", default=DEFAULT_BIND)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--out", help="Optional JSONL file to append received telemetry records.")
    parser.add_argument("--raw", action="store_true", help="Print raw device JSON instead of readable lines.")
    parser.add_argument("--max-bytes", type=int, default=4096)
    listen(parser.parse_args())


if __name__ == "__main__":
    main()
