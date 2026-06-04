from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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


@dataclass(frozen=True)
class EndingMeeting:
    meeting_id: str
    topic: str
    end_time: datetime
    end_time_raw: str
    minutes_until_end: int


@dataclass(frozen=True)
class ScheduleStatus:
    upcoming: UpcomingMeeting | None
    ending: EndingMeeting | None


class ScheduleWatcher:
    def __init__(self, config: ServerConfig, light: LightController) -> None:
        self._config = config
        self._light = light
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="schedule-watcher", daemon=True)
        self._last_key: tuple[str, int | None, str, int | None, bool] | None = None

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
            f"ending={self._config.ending_soon_minutes}m "
            f"poll={self._config.schedule_poll_seconds}s",
            flush=True,
        )

    def stop(self) -> None:
        self._stop.set()

    def run_once(self) -> None:
        status = self._schedule_status()
        snapshot = self._light.snapshot()
        ending = status.ending if snapshot.get("in_use") else None
        key = (
            status.upcoming.meeting_id if status.upcoming else "",
            status.upcoming.minutes_until_start if status.upcoming else None,
            ending.meeting_id if ending else "",
            ending.minutes_until_end if ending else None,
            bool(snapshot.get("in_use")),
        )
        if key == self._last_key:
            return
        self._last_key = key

        if ending is None:
            self._light.update_end_warning(
                meeting_id="",
                topic="",
                end_time="",
                minutes_until_end=None,
            )
        else:
            self._light.update_end_warning(
                meeting_id=ending.meeting_id,
                topic=ending.topic,
                end_time=ending.end_time_raw,
                minutes_until_end=ending.minutes_until_end,
            )

        if status.upcoming is None:
            self._light.update_schedule_warning(
                meeting_id="",
                topic="",
                start_time="",
                minutes_until_start=None,
            )
            return

        self._light.update_schedule_warning(
            meeting_id=status.upcoming.meeting_id,
            topic=status.upcoming.topic,
            start_time=status.upcoming.start_time_raw,
            minutes_until_start=status.upcoming.minutes_until_start,
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

    def _schedule_status(self) -> ScheduleStatus:
        access_token = get_access_token()
        meetings = sort_meetings(
            list_meetings(access_token, self._config.schedule_user_id, "upcoming")
        )
        now = datetime.now(timezone.utc)
        return ScheduleStatus(
            upcoming=self._next_upcoming_meeting(meetings, now),
            ending=self._ending_soon_meeting(meetings, now),
        )

    def _next_upcoming_meeting(
        self, meetings: list[dict[str, Any]], now: datetime
    ) -> UpcomingMeeting | None:
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

    def _ending_soon_meeting(
        self, meetings: list[dict[str, Any]], now: datetime
    ) -> EndingMeeting | None:
        lookahead_seconds = self._config.ending_soon_minutes * 60

        for meeting in meetings:
            parsed = parse_zoom_time(meeting.get("start_time"))
            duration = self._duration_minutes(meeting)
            if parsed is None or duration is None:
                continue

            start_time = parsed.astimezone(timezone.utc)
            end_time = start_time + timedelta(minutes=duration)
            seconds_until_start = (start_time - now).total_seconds()
            seconds_until_end = (end_time - now).total_seconds()

            if seconds_until_start > 0 or seconds_until_end < 0:
                continue
            if seconds_until_end > lookahead_seconds:
                return None

            return EndingMeeting(
                meeting_id=str(meeting.get("id") or meeting.get("uuid") or ""),
                topic=str(meeting.get("topic") or ""),
                end_time=end_time,
                end_time_raw=end_time.isoformat(),
                minutes_until_end=max(0, int(seconds_until_end // 60)),
            )

        return None

    @staticmethod
    def _duration_minutes(meeting: dict[str, Any]) -> int | None:
        try:
            duration = int(meeting.get("duration"))
        except (TypeError, ValueError):
            return None
        if duration <= 0:
            return None
        return duration

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
