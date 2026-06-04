#!/usr/bin/env python3
"""Run the local Zoom webhook server for the room-status light."""

from __future__ import annotations

import argparse

from zoom_light.config import load_config
from zoom_light.server import ZoomLightServer


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a local Zoom room-light webhook server.")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config(host=args.host, port=args.port)
    server = ZoomLightServer((config.host, config.port), config)

    print(f"Zoom light webhook server listening on http://{config.host}:{config.port}", flush=True)
    print("Zoom webhook path: /zoom/webhook", flush=True)
    print("Live dashboard path: /", flush=True)
    print("State stream path: /events", flush=True)
    print("Hardware/demo state path: /state", flush=True)
    print("Try: /simulate/start and /simulate/end", flush=True)
    server.light.print_state(server.light.snapshot())
    server.light.publish_current_state()
    server.schedule.start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server.", flush=True)
        return 0
    finally:
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
