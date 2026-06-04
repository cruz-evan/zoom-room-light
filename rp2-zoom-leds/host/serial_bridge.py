from __future__ import annotations

import argparse
import glob
import os
import sys
import time
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import serial
from serial.tools import list_ports

from device.serial_protocol import encode_command, normalize_command, normalize_rgb


DEFAULT_BAUD = 115200


def list_candidate_ports() -> list[str]:
    scored: list[tuple[int, str]] = []

    for info in list_ports.comports():
        fields = (
            info.device,
            info.description,
            info.manufacturer,
            getattr(info, "product", ""),
        )
        haystack = " ".join(str(field or "") for field in fields).lower()

        score = 0
        for keyword in ("pico", "rp2", "rp2040", "rp2350", "micropython", "raspberry"):
            if keyword in haystack:
                score += 5
        if info.device.startswith("/dev/cu.usbmodem"):
            score += 4
        if info.device.startswith("/dev/tty.usbmodem"):
            score += 2

        if score:
            scored.append((score, info.device))

    if not scored:
        for path in glob.glob("/dev/cu.usbmodem*") + glob.glob("/dev/tty.usbmodem*"):
            scored.append((1, path))

    return [device for _, device in sorted(scored, key=lambda item: (-item[0], item[1]))]


def resolve_port(port: str) -> str:
    if port and port != "auto":
        return port

    candidates = list_candidate_ports()
    if not candidates:
        raise RuntimeError(
            "No candidate RP2 USB serial port found. Pass --port /dev/cu.usbmodemXXXX."
        )
    return candidates[0]


def parse_rgb(value: str) -> list[int]:
    parts = [part.strip() for part in value.split(",")]
    return normalize_rgb(parts)


class SerialBridge:
    def __init__(
        self,
        port: str = "auto",
        baud: int = DEFAULT_BAUD,
        timeout: float = 1.0,
        settle_seconds: float = 1.2,
    ):
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self.settle_seconds = settle_seconds
        self.resolved_port: str | None = None
        self.connection: serial.Serial | None = None

    def open(self) -> "SerialBridge":
        self.resolved_port = resolve_port(self.port)
        self.connection = serial.Serial(
            self.resolved_port,
            self.baud,
            timeout=self.timeout,
            write_timeout=self.timeout,
        )
        time.sleep(self.settle_seconds)
        return self

    def send_command(self, command: dict[str, Any]) -> None:
        if self.connection is None:
            self.open()

        assert self.connection is not None
        payload = encode_command(command).encode("utf-8")
        self.connection.write(payload)
        self.connection.flush()

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def __enter__(self) -> "SerialBridge":
        return self.open()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def build_command(args: argparse.Namespace) -> dict[str, Any]:
    if args.mode == "off":
        return {"mode": "off"}
    if args.mode == "solid":
        return {"mode": "solid", "rgb": parse_rgb(args.rgb)}
    if args.mode == "pulse":
        return {"mode": "pulse", "rgb": parse_rgb(args.rgb), "speed": args.speed}
    if args.mode == "meeting_status":
        return {
            "mode": "meeting_status",
            "state": args.state,
            "minutes": args.minutes,
            "threshold": args.threshold,
        }
    return {
        "mode": "meeting",
        "active": args.active,
        "participants": args.participants,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Send one LED command to an RP2 board.")
    parser.add_argument("--port", default=os.getenv("RP2_PORT", "auto"))
    parser.add_argument("--baud", type=int, default=int(os.getenv("RP2_BAUD", DEFAULT_BAUD)))
    parser.add_argument(
        "--mode",
        choices=("off", "solid", "pulse", "meeting", "meeting_status"),
        default="solid",
    )
    parser.add_argument("--rgb", default="255,0,0", help="Comma-separated RGB, e.g. 0,120,255")
    parser.add_argument("--speed", type=float, default=0.6)
    parser.add_argument("--active", action="store_true")
    parser.add_argument("--participants", type=int, default=1)
    parser.add_argument(
        "--state",
        choices=("starting_soon", "in_progress", "ending_soon"),
        default="in_progress",
    )
    parser.add_argument("--minutes", type=float, default=5.0)
    parser.add_argument("--threshold", type=float, default=5.0)
    parser.add_argument("--list-ports", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.list_ports:
        for port in list_candidate_ports():
            print(port)
        return

    command = normalize_command(build_command(args))
    if args.dry_run:
        print(encode_command(command), end="")
        return

    with SerialBridge(args.port, args.baud) as bridge:
        bridge.send_command(command)
        print(f"Sent to {bridge.resolved_port}: {command}")


if __name__ == "__main__":
    main()
