#!/usr/bin/env python3
"""
Fetch scheduled Zoom meetings for a host user.

Setup:
  1. Create a Zoom OAuth app with meeting read scopes, or create a
     Server-to-Server OAuth app if you are an account admin.
  2. Set either:

       ZOOM_ACCESS_TOKEN=your_existing_oauth_access_token

     or all three server-to-server variables:

       ZOOM_ACCOUNT_ID=your_account_id
       ZOOM_CLIENT_ID=your_client_id
       ZOOM_CLIENT_SECRET=your_client_secret

Usage:
  python zoom_schedule.py
  python zoom_schedule.py --user your.email@example.com --format csv --output meetings.csv
  python zoom_schedule.py --type upcoming --timezone America/Vancouver

Note:
  Zoom's meetings endpoint returns meetings scheduled by or for the host user.
  It does not return every Zoom meeting you have been invited to from calendar
  invites unless those meetings are hosted on the queried Zoom account/user.
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


ZOOM_API_BASE = "https://api.zoom.us/v2"
ZOOM_TOKEN_URL = "https://zoom.us/oauth/token"


class ZoomApiError(RuntimeError):
    """Raised when Zoom returns an error response."""


def load_dotenv(path: str = ".env") -> None:
    """Load simple KEY=VALUE lines from .env without requiring a dependency."""
    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
) -> dict[str, Any]:
    request = Request(url, data=data, headers=headers or {}, method=method)

    try:
        with urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(detail)
            message = parsed.get("message") or parsed.get("reason") or detail
            code = parsed.get("code")
            if code:
                message = f"{message} (Zoom error code {code})"
        except json.JSONDecodeError:
            message = detail or str(exc)
        raise ZoomApiError(f"Zoom API request failed: HTTP {exc.code}: {message}") from exc
    except URLError as exc:
        raise ZoomApiError(f"Could not reach Zoom: {exc.reason}") from exc

    if not body:
        return {}

    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise ZoomApiError(f"Zoom returned non-JSON response: {body[:300]}") from exc


def get_access_token() -> str:
    if token := os.getenv("ZOOM_ACCESS_TOKEN"):
        return token

    account_id = os.getenv("ZOOM_ACCOUNT_ID")
    client_id = os.getenv("ZOOM_CLIENT_ID")
    client_secret = os.getenv("ZOOM_CLIENT_SECRET")
    if not all([account_id, client_id, client_secret]):
        raise ZoomApiError(
            "Missing Zoom credentials. Set ZOOM_ACCESS_TOKEN, or set "
            "ZOOM_ACCOUNT_ID, ZOOM_CLIENT_ID, and ZOOM_CLIENT_SECRET."
        )

    credentials = f"{client_id}:{client_secret}".encode("utf-8")
    basic_token = base64.b64encode(credentials).decode("ascii")
    form = urlencode(
        {"grant_type": "account_credentials", "account_id": account_id}
    ).encode("ascii")

    response = request_json(
        "POST",
        ZOOM_TOKEN_URL,
        headers={
            "Authorization": f"Basic {basic_token}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data=form,
    )

    access_token = response.get("access_token")
    if not access_token:
        raise ZoomApiError("Zoom token response did not include an access_token.")
    return str(access_token)


def list_meetings(access_token: str, user_id: str, meeting_type: str) -> list[dict[str, Any]]:
    meetings: list[dict[str, Any]] = []
    next_page_token = ""

    while True:
        params = {
            "type": meeting_type,
            "page_size": "300",
        }
        if next_page_token:
            params["next_page_token"] = next_page_token

        url = f"{ZOOM_API_BASE}/users/{user_id}/meetings?{urlencode(params)}"
        response = request_json(
            "GET",
            url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
        )

        meetings.extend(response.get("meetings", []))
        next_page_token = response.get("next_page_token") or ""
        if not next_page_token:
            break

    return meetings


def parse_zoom_time(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def format_zoom_time(value: str | None, target_timezone: ZoneInfo) -> str:
    parsed = parse_zoom_time(value)
    if parsed is None:
        return ""
    return parsed.astimezone(target_timezone).strftime("%Y-%m-%d %H:%M %Z")


def normalized_meeting(
    meeting: dict[str, Any], target_timezone: ZoneInfo
) -> dict[str, str]:
    meeting_id = meeting.get("id", "")
    return {
        "start_time": format_zoom_time(meeting.get("start_time"), target_timezone),
        "duration_min": str(meeting.get("duration", "")),
        "topic": str(meeting.get("topic", "")),
        "meeting_id": str(meeting_id),
        "join_url": str(meeting.get("join_url", "")),
        "timezone": str(meeting.get("timezone", "")),
        "type": str(meeting.get("type", "")),
    }


def sort_meetings(meetings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def sort_key(meeting: dict[str, Any]) -> tuple[int, datetime]:
        parsed = parse_zoom_time(meeting.get("start_time"))
        if parsed is None:
            return (1, datetime.max.replace(tzinfo=timezone.utc))
        return (0, parsed.astimezone(timezone.utc))

    return sorted(meetings, key=sort_key)


def print_table(rows: list[dict[str, str]]) -> None:
    if not rows:
        print("No meetings found.")
        return

    columns = ["start_time", "duration_min", "topic", "meeting_id", "join_url"]
    widths = {
        column: max(len(column), *(len(row[column]) for row in rows))
        for column in columns
    }

    header = "  ".join(column.ljust(widths[column]) for column in columns)
    divider = "  ".join("-" * widths[column] for column in columns)
    print(header)
    print(divider)
    for row in rows:
        print("  ".join(row[column].ljust(widths[column]) for column in columns))


def write_csv(rows: list[dict[str, str]], output_path: str | None) -> None:
    fieldnames = [
        "start_time",
        "duration_min",
        "topic",
        "meeting_id",
        "join_url",
        "timezone",
        "type",
    ]
    output = open(output_path, "w", newline="", encoding="utf-8") if output_path else sys.stdout
    try:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    finally:
        if output_path:
            output.close()


def write_json(rows: list[dict[str, str]], output_path: str | None) -> None:
    body = json.dumps(rows, indent=2)
    if output_path:
        Path(output_path).write_text(body + "\n", encoding="utf-8")
    else:
        print(body)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch your Zoom meeting schedule.")
    parser.add_argument(
        "--user",
        default=os.getenv("ZOOM_USER_ID", "me"),
        help="Zoom user ID or email. Use 'me' for user-level OAuth. Defaults to ZOOM_USER_ID or me.",
    )
    parser.add_argument(
        "--type",
        default="scheduled",
        choices=["scheduled", "upcoming", "upcoming_meetings", "previous_meetings"],
        help="Zoom meeting list type. Defaults to scheduled.",
    )
    parser.add_argument(
        "--format",
        default="table",
        choices=["table", "csv", "json"],
        help="Output format. Defaults to table.",
    )
    parser.add_argument(
        "--output",
        help="Write csv/json output to this file instead of stdout.",
    )
    parser.add_argument(
        "--timezone",
        default=os.getenv("TZ", "America/Vancouver"),
        help="Timezone for displayed start times. Defaults to TZ or America/Vancouver.",
    )
    return parser.parse_args()


def main() -> int:
    load_dotenv()
    args = parse_args()

    try:
        target_timezone = ZoneInfo(args.timezone)
    except ZoneInfoNotFoundError:
        print(f"Unknown timezone: {args.timezone}", file=sys.stderr)
        return 2

    try:
        access_token = get_access_token()
        meetings = sort_meetings(list_meetings(access_token, args.user, args.type))
    except ZoomApiError as exc:
        print(exc, file=sys.stderr)
        return 1

    rows = [normalized_meeting(meeting, target_timezone) for meeting in meetings]
    if args.format == "csv":
        write_csv(rows, args.output)
    elif args.format == "json":
        write_json(rows, args.output)
    else:
        if args.output:
            print("--output is only supported with --format csv or --format json.", file=sys.stderr)
            return 2
        print_table(rows)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
