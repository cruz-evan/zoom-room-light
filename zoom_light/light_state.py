from __future__ import annotations

import json
import queue
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


GREEN = "green"
YELLOW = "yellow"
RED = "red"
PURPLE = "purple"


@dataclass
class LightState:
    color: str = GREEN
    label: str = "FREE"
    in_use: bool = False
    active_meeting_id: str = ""
    active_topic: str = ""
    next_meeting_id: str = ""
    next_meeting_topic: str = ""
    next_meeting_start_time: str = ""
    minutes_until_next: int | None = None
    last_event: str = "server.started"
    updated_at: str = ""


@dataclass
class LightEvent:
    event: str
    meeting_id: str
    topic: str
    color: str
    received_at: str


class LightController:
    def __init__(self) -> None:
        self._state = LightState(updated_at=self._utc_now())
        self._lock = threading.Lock()
        self._subscribers: set[queue.Queue[str]] = set()
        self._recent_events: list[LightEvent] = []
        self._active_participants: dict[str, set[str]] = {}

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            snapshot = asdict(self._state)
            snapshot["recent_events"] = [asdict(event) for event in self._recent_events]
            return snapshot

    def subscribe(self) -> queue.Queue[str]:
        subscriber: queue.Queue[str] = queue.Queue()
        with self._lock:
            self._subscribers.add(subscriber)
            snapshot = asdict(self._state)
            snapshot["recent_events"] = [asdict(event) for event in self._recent_events]
            subscriber.put(json.dumps(snapshot))
        return subscriber

    def unsubscribe(self, subscriber: queue.Queue[str]) -> None:
        with self._lock:
            self._subscribers.discard(subscriber)

    def update_from_zoom_event(self, event: str, payload: dict[str, Any]) -> dict[str, Any]:
        meeting = self._extract_meeting(payload)
        meeting_id = str(meeting.get("id") or meeting.get("uuid") or "")
        topic = str(meeting.get("topic") or "")
        participant_key = self._participant_key(meeting)

        with self._lock:
            if event == "meeting.started":
                self._state.color = RED
                self._state.label = "IN USE"
                self._state.in_use = True
                self._state.active_meeting_id = meeting_id
                self._state.active_topic = topic
                self._clear_next_meeting()
            elif event == "meeting.ended":
                if meeting_id:
                    self._active_participants.pop(meeting_id, None)
                self._state.in_use = False
                self._state.active_meeting_id = ""
                self._state.active_topic = ""
                self._apply_idle_color()
            elif event == "meeting.participant_joined":
                if meeting_id and participant_key:
                    self._active_participants.setdefault(meeting_id, set()).add(participant_key)
                self._state.color = RED
                self._state.label = "IN USE"
                self._state.in_use = True
                self._state.active_meeting_id = meeting_id
                self._clear_next_meeting()
                if topic:
                    self._state.active_topic = topic
            elif event == "meeting.participant_left":
                if meeting_id and participant_key:
                    self._active_participants.setdefault(meeting_id, set()).discard(participant_key)
                remaining = len(self._active_participants.get(meeting_id, set())) if meeting_id else 0
                if remaining == 0:
                    self._state.in_use = False
                    self._state.active_meeting_id = ""
                    self._state.active_topic = ""
                    self._apply_idle_color()
                else:
                    self._state.color = RED
                    self._state.label = "IN USE"
                    self._state.in_use = True
            elif event == "meeting.updated":
                if topic:
                    self._state.active_topic = topic
                if self._state.in_use:
                    self._state.color = RED
                    self._state.label = "IN USE"
                else:
                    self._apply_idle_color()
            elif event == "manual.reset":
                self._active_participants.clear()
                self._state.in_use = False
                self._state.active_meeting_id = ""
                self._state.active_topic = ""
                self._clear_next_meeting()
                self._apply_idle_color()
            else:
                self._state.color = PURPLE
                self._state.label = "UNKNOWN EVENT"

            self._state.last_event = event
            self._state.updated_at = self._utc_now()
            self._record_event(event, meeting_id, topic)
            snapshot = asdict(self._state)
            snapshot["recent_events"] = [asdict(item) for item in self._recent_events]
            message = json.dumps(snapshot)
            subscribers = list(self._subscribers)

        for subscriber in subscribers:
            subscriber.put(message)

        self.print_state(snapshot)
        return snapshot

    def update_schedule_warning(
        self,
        *,
        meeting_id: str,
        topic: str,
        start_time: str,
        minutes_until_start: int | None,
    ) -> dict[str, Any]:
        with self._lock:
            self._state.next_meeting_id = meeting_id
            self._state.next_meeting_topic = topic
            self._state.next_meeting_start_time = start_time
            self._state.minutes_until_next = minutes_until_start
            self._state.last_event = "schedule.upcoming" if meeting_id else "schedule.clear"
            self._state.updated_at = self._utc_now()
            if not self._state.in_use:
                self._apply_idle_color()
            self._record_event(
                self._state.last_event,
                meeting_id,
                topic or self._state.next_meeting_start_time,
            )
            snapshot = asdict(self._state)
            snapshot["recent_events"] = [asdict(item) for item in self._recent_events]
            message = json.dumps(snapshot)
            subscribers = list(self._subscribers)

        for subscriber in subscribers:
            subscriber.put(message)

        self.print_state(snapshot)
        return snapshot

    @staticmethod
    def print_state(state: dict[str, Any]) -> None:
        topic = f" | {state['active_topic']}" if state["active_topic"] else ""
        meeting = f" | meeting={state['active_meeting_id']}" if state["active_meeting_id"] else ""
        print(
            f"[{state['updated_at']}] LIGHT={state['color'].upper()} "
            f"STATUS={state['label']} EVENT={state['last_event']}{meeting}{topic}",
            flush=True,
        )

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _extract_meeting(payload: dict[str, Any]) -> dict[str, Any]:
        obj = payload.get("object")
        if isinstance(obj, dict):
            return obj
        return {}

    @staticmethod
    def _participant_key(meeting: dict[str, Any]) -> str:
        participant = meeting.get("participant")
        if not isinstance(participant, dict):
            return ""
        return str(
            participant.get("user_id")
            or participant.get("id")
            or participant.get("email")
            or participant.get("user_name")
            or ""
        )

    def _record_event(self, event: str, meeting_id: str, topic: str) -> None:
        self._recent_events.insert(
            0,
            LightEvent(
                event=event,
                meeting_id=meeting_id,
                topic=topic,
                color=self._state.color,
                received_at=self._state.updated_at,
            ),
        )
        del self._recent_events[12:]

    def _apply_idle_color(self) -> None:
        if self._state.next_meeting_id:
            self._state.color = YELLOW
            self._state.label = "STARTS SOON"
        else:
            self._state.color = GREEN
            self._state.label = "FREE"

    def _clear_next_meeting(self) -> None:
        self._state.next_meeting_id = ""
        self._state.next_meeting_topic = ""
        self._state.next_meeting_start_time = ""
        self._state.minutes_until_next = None
