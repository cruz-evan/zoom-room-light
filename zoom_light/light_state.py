from __future__ import annotations

import json
import queue
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


GREEN = "green"
YELLOW = "yellow"
ORANGE = "orange"
RED = "red"
PURPLE = "purple"


@dataclass
class LightState:
    color: str = GREEN
    label: str = "FREE"
    in_use: bool = False
    active_meeting_id: str = ""
    active_topic: str = ""
    active_meeting_end_time: str = ""
    minutes_until_end: int | None = None
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
    def __init__(self, hardware_output: Any | None = None) -> None:
        self._state = LightState(updated_at=self._utc_now())
        self._lock = threading.Lock()
        self._publish_lock = threading.Lock()
        self._subscribers: set[queue.Queue[str]] = set()
        self._recent_events: list[LightEvent] = []
        self._active_participants: dict[str, set[str]] = {}
        self._hardware_output = hardware_output

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
            previous = asdict(self._state)
            if event == "meeting.started":
                self._state.in_use = True
                self._state.active_meeting_id = meeting_id
                self._state.active_topic = topic
                self._clear_end_warning()
                self._clear_next_meeting()
                self._apply_active_color()
            elif event == "meeting.ended":
                active_meeting_id = self._state.active_meeting_id
                if active_meeting_id and meeting_id and meeting_id != active_meeting_id:
                    pass
                else:
                    if meeting_id:
                        self._active_participants.pop(meeting_id, None)
                    self._state.in_use = False
                    self._state.active_meeting_id = ""
                    self._state.active_topic = ""
                    self._clear_end_warning()
                    self._apply_idle_color()
            elif event == "meeting.participant_joined":
                if meeting_id and participant_key:
                    self._active_participants.setdefault(meeting_id, set()).add(participant_key)
            elif event == "meeting.participant_left":
                if meeting_id and participant_key:
                    self._active_participants.setdefault(meeting_id, set()).discard(participant_key)
                if meeting_id and not self._active_participants.get(meeting_id):
                    self._active_participants.pop(meeting_id, None)
            elif event == "meeting.updated":
                if topic:
                    self._state.active_topic = topic
                if self._state.in_use:
                    self._apply_active_color()
                else:
                    self._apply_idle_color()
            elif event == "manual.reset":
                self._active_participants.clear()
                self._state.in_use = False
                self._state.active_meeting_id = ""
                self._state.active_topic = ""
                self._clear_end_warning()
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

        self.print_transition("zoom_event", event, previous, snapshot)
        self.print_state(snapshot)
        self._publish_hardware(snapshot)
        return snapshot

    def update_end_warning(
        self,
        *,
        meeting_id: str,
        topic: str,
        end_time: str,
        minutes_until_end: int | None,
    ) -> dict[str, Any]:
        with self._lock:
            before = (
                self._state.active_meeting_end_time,
                self._state.minutes_until_end,
                self._state.color,
                self._state.label,
            )
            self._state.active_meeting_end_time = end_time
            self._state.minutes_until_end = minutes_until_end
            self._state.last_event = "schedule.ending_soon" if minutes_until_end is not None else "schedule.end_clear"
            self._state.updated_at = self._utc_now()

            if self._state.in_use:
                if meeting_id:
                    self._state.active_meeting_id = meeting_id
                if topic:
                    self._state.active_topic = topic
                self._apply_active_color()

            after = (
                self._state.active_meeting_end_time,
                self._state.minutes_until_end,
                self._state.color,
                self._state.label,
            )
            snapshot = asdict(self._state)
            snapshot["recent_events"] = [asdict(item) for item in self._recent_events]
            if before == after:
                return snapshot

            self._record_event(self._state.last_event, meeting_id, topic or end_time)
            snapshot = asdict(self._state)
            snapshot["recent_events"] = [asdict(item) for item in self._recent_events]
            message = json.dumps(snapshot)
            subscribers = list(self._subscribers)

        for subscriber in subscribers:
            subscriber.put(message)

        self.print_state(snapshot)
        self._publish_hardware(snapshot)
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
        self._publish_hardware(snapshot)
        return snapshot

    def publish_current_state(self) -> None:
        self._publish_hardware(self.snapshot())

    def close(self) -> None:
        output = self._hardware_output
        if output is not None and hasattr(output, "close"):
            output.close()

    @staticmethod
    def print_state(state: dict[str, Any]) -> None:
        topic = f" | {state['active_topic']}" if state["active_topic"] else ""
        meeting = f" | meeting={state['active_meeting_id']}" if state["active_meeting_id"] else ""
        ending = (
            f" | ending_in={state['minutes_until_end']}m"
            if state.get("minutes_until_end") is not None
            else ""
        )
        print(
            f"[{state['updated_at']}] LIGHT={state['color'].upper()} "
            f"STATUS={state['label']} EVENT={state['last_event']}{meeting}{topic}{ending}",
            flush=True,
        )

    @staticmethod
    def print_transition(
        source: str,
        reason: str,
        before: dict[str, Any],
        after: dict[str, Any],
    ) -> None:
        print(
            f"[{after['updated_at']}] TRANSITION source={source} reason={reason} "
            f"from={LightController._state_marker(before)} "
            f"to={LightController._state_marker(after)}",
            flush=True,
        )

    @staticmethod
    def _state_marker(state: dict[str, Any]) -> str:
        if state.get("in_use"):
            status = "active"
        elif state.get("next_meeting_id"):
            status = "upcoming"
        else:
            status = "idle"

        active = state.get("active_meeting_id") or "-"
        next_meeting = state.get("next_meeting_id") or "-"
        ending = state.get("minutes_until_end")
        ending_text = "-" if ending is None else f"{ending}m"
        return (
            f"{status}/{state.get('label', '')}"
            f"/{state.get('color', '')}"
            f"/active={active}"
            f"/next={next_meeting}"
            f"/ending={ending_text}"
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

    def _apply_active_color(self) -> None:
        if self._state.minutes_until_end is not None:
            self._state.color = ORANGE
            self._state.label = "ENDING SOON"
        else:
            self._state.color = RED
            self._state.label = "IN USE"

    def _clear_end_warning(self) -> None:
        self._state.active_meeting_end_time = ""
        self._state.minutes_until_end = None

    def _clear_next_meeting(self) -> None:
        self._state.next_meeting_id = ""
        self._state.next_meeting_topic = ""
        self._state.next_meeting_start_time = ""
        self._state.minutes_until_next = None

    def _publish_hardware(self, snapshot: dict[str, Any]) -> None:
        if self._hardware_output is None:
            return
        try:
            with self._publish_lock:
                self._hardware_output.publish(self.snapshot())
        except Exception as exc:
            print(f"Hardware output failed: {exc}", flush=True)
