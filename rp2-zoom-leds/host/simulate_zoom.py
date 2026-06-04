from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from device.serial_protocol import encode_command
from host.serial_bridge import DEFAULT_BAUD, SerialBridge


FAKE_SEQUENCE = (
    ("starting in 5m", {"mode": "meeting_status", "state": "starting_soon", "minutes": 5}),
    ("in progress", {"mode": "meeting_status", "state": "in_progress"}),
    ("ending in 5m", {"mode": "meeting_status", "state": "ending_soon", "minutes": 5}),
    ("ending in 1m", {"mode": "meeting_status", "state": "ending_soon", "minutes": 1}),
    ("off", {"mode": "off"}),
)


def iter_sequence(loops: int):
    completed = 0
    while loops == 0 or completed < loops:
        for label, command in FAKE_SEQUENCE:
            yield label, command
        completed += 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Send fake Zoom states to the RP2.")
    parser.add_argument("--port", default=os.getenv("RP2_PORT", "auto"))
    parser.add_argument("--baud", type=int, default=int(os.getenv("RP2_BAUD", DEFAULT_BAUD)))
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--loops", type=int, default=0, help="0 means run forever")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.dry_run:
        for label, command in iter_sequence(args.loops or 1):
            print(f"{label}: {encode_command(command).strip()}")
            time.sleep(0 if args.interval < 0 else min(args.interval, 0.1))
        return

    with SerialBridge(args.port, args.baud) as bridge:
        print(f"Sending fake Zoom states to {bridge.resolved_port}. Press Ctrl+C to stop.")
        for label, command in iter_sequence(args.loops):
            print(f"{label}: {command}")
            bridge.send_command(command)
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
