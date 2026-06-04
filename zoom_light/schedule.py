from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from zoom_schedule import ZoomApiError, get_access_token, list_meetings, parse_zoom_time, sort_meetings

from .config import ServerConfig
from .light_state import LightController


@dataclass(frozen=True)
class UpcomingMeeting:
    meeting_id: str
    topic: str
    start_time: datetime
    start_time_raw: str
    minutes_until_start: int


class ScheduleWatcher:
    def __init__(self, config: ServerConfig, light: LightController) -> None:
        self._config = config
        self._light = light
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="schedule-watcher", daemon=True)
        self._last_key: tuple[str, int | None] | None = None

    def start(self) -> None:
        if not self._has_credentials():
            print(
                "Schedule watcher disabled: set ZOOM_ACCESS_TOKEN or "
                "ZOOM_ACCOUNT_ID/ZOOM_CLIENT_ID/ZOOM_CLIENT_SECRET.",
                flush=True,
            )
            return
        self._thread.start()
        print(
            "Schedule watcher enabled: "
            f"user={self._config.schedule_user_id} "
            f"lookahead={self._config.schedule_lookahead_minutes}m "
            f"poll={self._config.schedule_poll_seconds}s",
            flush=True,
        )

    def stop(self) -> None:
        self._stop.set()

    def run_once(self) -> None:
        upcoming = self._next_upcoming_meeting()
        key = (upcoming.meeting_id, upcoming.minutes_until_start) if upcoming else ("", None)
        if key == self._last_key:
            return
        self._last_key = key

        if upcoming is None:
            self._light.update_schedule_warning(
                meeting_id="",
                topic="",
                start_time="",
                minutes_until_start=None,
            )
            return

        self._light.update_schedule_warning(
            meeting_id=upcoming.meeting_id,
            topic=upcoming.topic,
            start_time=upcoming.start_time_raw,
            minutes_until_start=upcoming.minutes_until_start,
        )

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except ZoomApiError as exc:
                print(f"Schedule watcher error: {exc}", flush=True)
            except Exception as exc:
                print(f"Schedule watcher unexpected error: {exc}", flush=True)
            self._stop.wait(self._config.schedule_poll_seconds)

    def _next_upcoming_meeting(self) -> UpcomingMeeting | None:
        access_token = get_access_token()
        meetings = sort_meetings(
            list_meetings(access_token, self._config.schedule_user_id, "upcoming")
        )
        now = datetime.now(timezone.utc)
        lookahead_seconds = self._config.schedule_lookahead_minutes * 60

        for meeting in meetings:
            parsed = parse_zoom_time(meeting.get("start_time"))
            if parsed is None:
                continue

            start_time = parsed.astimezone(timezone.utc)
            seconds_until_start = (start_time - now).total_seconds()
            if seconds_until_start < 0:
                continue
            if seconds_until_start > lookahead_seconds:
                return None

            return UpcomingMeeting(
                meeting_id=str(meeting.get("id") or meeting.get("uuid") or ""),
                topic=str(meeting.get("topic") or ""),
                start_time=start_time,
                start_time_raw=str(meeting.get("start_time") or start_time.isoformat()),
                minutes_until_start=max(0, int(seconds_until_start // 60)),
            )

        return None

    @staticmethod
    def _has_credentials() -> bool:
        if os.getenv("ZOOM_ACCESS_TOKEN"):
            return True
        return all(
            [
                os.getenv("ZOOM_ACCOUNT_ID"),
                os.getenv("ZOOM_CLIENT_ID"),
                os.getenv("ZOOM_CLIENT_SECRET"),
            ]
        )
