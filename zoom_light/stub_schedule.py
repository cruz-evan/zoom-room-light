from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from zoom_schedule import parse_zoom_time, sort_meetings


class StubScheduleError(ValueError):
    """Raised when a stub schedule file or payload is invalid."""


class StubScheduleStore:
    def __init__(self, path: str) -> None:
        if not path:
            raise StubScheduleError("ZOOM_STUB_SCHEDULE_FILE is required for stub schedules.")
        self.path = Path(path)

    def ensure_file(self) -> None:
        if self.path.exists():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.write_meetings([])

    def read_meetings(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise StubScheduleError(f"Could not parse stub schedule JSON: {exc}") from exc

        if isinstance(raw, dict):
            meetings = raw.get("meetings", [])
        else:
            meetings = raw
        return self.normalize_meetings(meetings)

    def write_meetings(self, meetings: Any) -> list[dict[str, Any]]:
        normalized = self.normalize_meetings(meetings)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        body = {"meetings": normalized}
        self.path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
        return normalized

    @staticmethod
    def normalize_meetings(meetings: Any) -> list[dict[str, Any]]:
        if meetings is None:
            return []
        if not isinstance(meetings, list):
            raise StubScheduleError("Stub schedule must be a JSON list of meetings.")

        normalized: list[dict[str, Any]] = []
        for index, meeting in enumerate(meetings, start=1):
            if not isinstance(meeting, dict):
                raise StubScheduleError(f"Meeting {index} must be a JSON object.")

            start_time = str(meeting.get("start_time") or "").strip()
            if parse_zoom_time(start_time) is None:
                raise StubScheduleError(f"Meeting {index} needs an ISO start_time.")

            try:
                duration = int(meeting.get("duration", 30))
            except (TypeError, ValueError) as exc:
                raise StubScheduleError(f"Meeting {index} duration must be minutes.") from exc
            if duration <= 0:
                raise StubScheduleError(f"Meeting {index} duration must be positive.")

            meeting_id = str(meeting.get("id") or meeting.get("uuid") or f"stub-{index}")
            normalized.append(
                {
                    "id": meeting_id,
                    "uuid": str(meeting.get("uuid") or meeting_id),
                    "topic": str(meeting.get("topic") or f"Stub meeting {index}"),
                    "start_time": start_time,
                    "duration": duration,
                    "timezone": str(meeting.get("timezone") or "UTC"),
                    "type": str(meeting.get("type") or "scheduled"),
                    "join_url": str(meeting.get("join_url") or ""),
                }
            )

        return sort_meetings(normalized)
