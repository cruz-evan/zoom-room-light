from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests
from dotenv import load_dotenv

from host.serial_bridge import DEFAULT_BAUD, SerialBridge


TOKEN_URL = "https://zoom.us/oauth/token"
API_BASE_URL = "https://api.zoom.us"


@dataclass(frozen=True)
class ZoomConfig:
    account_id: str
    client_id: str
    client_secret: str
    user_id: str = "me"
    meeting_id: str = ""
    poll_interval: float = 15.0
    timeout: float = 10.0

    @classmethod
    def from_env(cls) -> "ZoomConfig":
        missing = [
            name
            for name in ("ZOOM_ACCOUNT_ID", "ZOOM_CLIENT_ID", "ZOOM_CLIENT_SECRET")
            if not os.getenv(name)
        ]
        if missing:
            raise RuntimeError(f"Missing required Zoom env vars: {', '.join(missing)}")

        return cls(
            account_id=os.environ["ZOOM_ACCOUNT_ID"],
            client_id=os.environ["ZOOM_CLIENT_ID"],
            client_secret=os.environ["ZOOM_CLIENT_SECRET"],
            user_id=os.getenv("ZOOM_USER_ID", "me"),
            meeting_id=os.getenv("ZOOM_MEETING_ID", ""),
            poll_interval=float(os.getenv("ZOOM_POLL_INTERVAL_SECONDS", "15")),
            timeout=float(os.getenv("ZOOM_REQUEST_TIMEOUT_SECONDS", "10")),
        )


class ZoomClient:
    def __init__(self, config: ZoomConfig):
        self.config = config
        self.session = requests.Session()
        self.access_token: str | None = None
        self.expires_at = 0.0

    def get_access_token(self) -> str:
        if self.access_token and time.time() < self.expires_at - 60:
            return self.access_token

        response = self.session.post(
            TOKEN_URL,
            auth=(self.config.client_id, self.config.client_secret),
            data={
                "grant_type": "account_credentials",
                "account_id": self.config.account_id,
            },
            timeout=self.config.timeout,
        )
        response.raise_for_status()
        payload = response.json()

        self.access_token = payload["access_token"]
        self.expires_at = time.time() + int(payload.get("expires_in", 3600))
        return self.access_token

    def request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        url = path if path.startswith("https://") else f"{API_BASE_URL}{path}"

        for attempt in range(4):
            token = self.get_access_token()
            headers = kwargs.pop("headers", {})
            headers["Authorization"] = f"Bearer {token}"

            response = self.session.request(
                method,
                url,
                headers=headers,
                timeout=self.config.timeout,
                **kwargs,
            )

            if response.status_code == 401 and attempt == 0:
                self.access_token = None
                continue

            if response.status_code == 429:
                delay = _retry_delay(response, attempt)
                print(f"Zoom rate limit hit; waiting {delay:.1f}s before retrying.")
                time.sleep(delay)
                continue

            response.raise_for_status()
            if not response.content:
                return {}
            return response.json()

        raise RuntimeError("Zoom API request failed after retries")

    def poll_meeting_state(self) -> dict[str, Any]:
        if self.config.meeting_id:
            payload = self.request(
                "GET",
                f"/v2/metrics/meetings/{self.config.meeting_id}/participants",
                params={"type": "live", "page_size": 300},
            )
            participants = len(payload.get("participants", []))
            return {"active": participants > 0, "participants": participants}

        payload = self.request(
            "GET",
            f"/v2/users/{self.config.user_id}/meetings",
            params={"type": "live", "page_size": 30},
        )
        meetings = payload.get("meetings", [])
        return {"active": bool(meetings), "participants": 0}


def _retry_delay(response: requests.Response, attempt: int) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return max(1.0, float(retry_after))
        except ValueError:
            pass
    return min(60.0, 2.0**attempt)


def command_from_meeting_state(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "mode": "meeting",
        "active": bool(state.get("active")),
        "participants": int(state.get("participants", 0)),
    }


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(description="Poll Zoom and send LED states to the RP2.")
    parser.add_argument("--port", default=os.getenv("RP2_PORT", "auto"))
    parser.add_argument("--baud", type=int, default=int(os.getenv("RP2_BAUD", DEFAULT_BAUD)))
    parser.add_argument(
        "--interval",
        type=float,
        default=float(os.getenv("ZOOM_POLL_INTERVAL_SECONDS", "15")),
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    config = ZoomConfig.from_env()
    client = ZoomClient(config)

    if args.dry_run:
        while True:
            state = client.poll_meeting_state()
            print(command_from_meeting_state(state))
            time.sleep(args.interval)

    with SerialBridge(args.port, args.baud) as bridge:
        print(f"Polling Zoom every {args.interval:.1f}s; sending to {bridge.resolved_port}.")
        while True:
            state = client.poll_meeting_state()
            command = command_from_meeting_state(state)
            print(command)
            bridge.send_command(command)
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
