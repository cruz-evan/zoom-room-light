from __future__ import annotations

import json
import queue
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .config import ServerConfig
from .dashboard import DASHBOARD_HTML
from .light_state import LightController
from .schedule import ScheduleWatcher
from .security import encrypted_validation_token, verify_zoom_signature
from .serial_output import SerialLightOutput, command_from_state
from .stub_schedule import StubScheduleError


class ZoomLightServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], config: ServerConfig) -> None:
        super().__init__(server_address, ZoomLightHandler)
        self.config = config
        self.hardware_output = SerialLightOutput.from_server_config(config)
        self.light = LightController(self.hardware_output)
        self.schedule = ScheduleWatcher(config, self.light)

    def server_close(self) -> None:
        if hasattr(self, "schedule"):
            self.schedule.stop()
        if hasattr(self, "light"):
            self.light.close()
        super().server_close()


class ZoomLightHandler(BaseHTTPRequestHandler):
    server: ZoomLightServer
    server_version = "ZoomLightWebhook/2.0"

    def handle(self) -> None:
        try:
            super().handle()
        except ConnectionResetError:
            return

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}", file=sys.stderr)

    def do_HEAD(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self.send_response(HTTPStatus.OK.value)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            return
        self.send_response(HTTPStatus.NOT_FOUND.value)
        self.end_headers()

    def do_GET(self) -> None:
        parsed_url = urlparse(self.path)
        path = parsed_url.path

        if path == "/":
            self.write_html(HTTPStatus.OK, DASHBOARD_HTML)
            return

        if path == "/state":
            self.write_json(HTTPStatus.OK, self.server.light.snapshot())
            return

        if path == "/test/schedule":
            if not self.require_test_control():
                return
            try:
                meetings = self.server.schedule.stub_meetings()
            except StubScheduleError as exc:
                self.write_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            self.write_json(HTTPStatus.OK, {"source": "stub", "meetings": meetings})
            return

        device_id = self.device_id_from_request(parsed_url)
        if path == "/device/state" or self.is_device_state_path(parsed_url):
            if not self.authorize_device():
                self.write_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                return
            snapshot = self.server.light.snapshot()
            command = command_from_state(snapshot)
            body = {
                "v": 1,
                "command": command,
                "poll_seconds": self.server.config.device_poll_seconds,
                "updated_at": snapshot.get("updated_at", ""),
                "last_event": snapshot.get("last_event", ""),
            }
            if device_id:
                body["device_id"] = device_id
            self.write_json(HTTPStatus.OK, body)
            return

        if path == "/events":
            self.stream_events()
            return

        if path == "/simulate/start":
            state = self.server.light.update_from_zoom_event(
                "meeting.started",
                {"object": {"id": "demo-meeting", "topic": "Hackathon demo"}},
            )
            self.write_json(HTTPStatus.OK, state)
            return

        if path == "/simulate/end":
            state = self.server.light.update_from_zoom_event(
                "meeting.ended",
                {"object": {"id": "demo-meeting"}},
            )
            self.write_json(HTTPStatus.OK, state)
            return

        if path == "/simulate/join":
            state = self.server.light.update_from_zoom_event(
                "meeting.participant_joined",
                {
                    "object": {
                        "id": "demo-meeting",
                        "topic": "Hackathon demo",
                        "participant": {"user_id": "demo-user", "user_name": "Demo User"},
                    }
                },
            )
            self.write_json(HTTPStatus.OK, state)
            return

        if path == "/simulate/leave":
            state = self.server.light.update_from_zoom_event(
                "meeting.participant_left",
                {
                    "object": {
                        "id": "demo-meeting",
                        "topic": "Hackathon demo",
                        "participant": {"user_id": "demo-user", "user_name": "Demo User"},
                    }
                },
            )
            self.write_json(HTTPStatus.OK, state)
            return

        if path == "/simulate/upcoming":
            state = self.server.light.update_schedule_warning(
                meeting_id="demo-upcoming",
                topic="Hackathon judging",
                start_time="demo",
                minutes_until_start=5,
            )
            self.write_json(HTTPStatus.OK, state)
            return

        if path == "/simulate/ending-soon":
            self.server.light.update_from_zoom_event(
                "meeting.started",
                {"object": {"id": "demo-meeting", "topic": "Hackathon demo"}},
            )
            state = self.server.light.update_end_warning(
                meeting_id="demo-meeting",
                topic="Hackathon demo",
                end_time="demo",
                minutes_until_end=5,
            )
            self.write_json(HTTPStatus.OK, state)
            return

        if path == "/simulate/clear-upcoming":
            state = self.server.light.update_schedule_warning(
                meeting_id="",
                topic="",
                start_time="",
                minutes_until_start=None,
            )
            self.write_json(HTTPStatus.OK, state)
            return

        if path == "/schedule/check":
            try:
                self.server.schedule.run_once()
            except Exception as exc:
                self.write_json(HTTPStatus.BAD_GATEWAY, {"error": str(exc)})
                return
            self.write_json(HTTPStatus.OK, self.server.light.snapshot())
            return

        if path == "/reset":
            state = self.server.light.update_from_zoom_event("manual.reset", {})
            self.write_json(HTTPStatus.OK, state)
            return

        self.write_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def authorize_device(self) -> bool:
        token = self.server.config.device_token
        if not token:
            return True

        header = self.headers.get("Authorization", "")
        return header == f"Bearer {token}"

    def device_id_from_request(self, parsed_url) -> str:
        if parsed_url.path == "/device/state":
            query_id = parse_qs(parsed_url.query).get("device_id", [""])[0].strip()
            if query_id:
                return query_id

            header_id = self.headers.get("X-Device-ID", "").strip()
            if header_id:
                return header_id

        parts = [part for part in parsed_url.path.split("/") if part]
        if len(parts) == 3 and parts[0] == "device" and parts[2] == "state":
            return parts[1].strip()

        return ""

    def is_device_state_path(self, parsed_url) -> bool:
        parts = [part for part in parsed_url.path.split("/") if part]
        return len(parts) == 3 and parts[0] == "device" and parts[2] == "state"

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/test/schedule":
            self.handle_test_schedule_update()
            return
        if path == "/test/zoom-event":
            self.handle_test_zoom_event()
            return

        if path != "/zoom/webhook":
            self.write_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return

        raw_body, body = self.read_json_body()
        if body is None:
            self.write_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_json"})
            return

        event = str(body.get("event") or "")
        payload = body.get("payload") if isinstance(body.get("payload"), dict) else {}
        secret_token = self.server.config.webhook_secret_token
        print(f"Received Zoom webhook event={event}", flush=True)

        if event == "endpoint.url_validation":
            self.handle_url_validation(payload, secret_token)
            return

        headers = {key.lower(): value for key, value in self.headers.items()}
        if secret_token and not verify_zoom_signature(raw_body, headers, secret_token):
            self.write_json(HTTPStatus.UNAUTHORIZED, {"error": "invalid_zoom_signature"})
            return

        if not secret_token:
            print("Warning: ZOOM_WEBHOOK_SECRET_TOKEN is not set; skipping signature checks.", flush=True)

        if event in ("meeting.created", "meeting.deleted"):
            try:
                self.server.schedule.run_once()
                state = self.server.light.snapshot()
            except Exception as exc:
                print(f"Schedule refresh after {event} failed: {exc}", flush=True)
                state = self.server.light.snapshot()
            self.write_json(HTTPStatus.OK, {"ok": True, "state": state})
            return

        state = self.server.light.update_from_zoom_event(event, payload)
        if event in ("meeting.ended", "meeting.participant_left", "meeting.updated"):
            try:
                self.server.schedule.run_once()
                state = self.server.light.snapshot()
            except Exception as exc:
                print(f"Schedule refresh after {event} failed: {exc}", flush=True)
        self.write_json(HTTPStatus.OK, {"ok": True, "state": state})

    def require_test_control(self) -> bool:
        if self.server.config.test_control_enabled:
            return True
        self.write_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
        return False

    def handle_test_schedule_update(self) -> None:
        if not self.require_test_control():
            return

        _, body = self.read_json_body()
        if body is None:
            self.write_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_json"})
            return

        meetings = body.get("meetings") if isinstance(body, dict) else None
        try:
            normalized = self.server.schedule.replace_stub_meetings(meetings)
            if body.get("refresh_schedule", True):
                self.server.schedule.run_once()
        except StubScheduleError as exc:
            self.write_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        except Exception as exc:
            self.write_json(HTTPStatus.BAD_GATEWAY, {"error": str(exc)})
            return

        self.write_json(
            HTTPStatus.OK,
            {
                "ok": True,
                "meetings": normalized,
                "state": self.server.light.snapshot(),
            },
        )

    def handle_test_zoom_event(self) -> None:
        if not self.require_test_control():
            return

        _, body = self.read_json_body()
        if body is None:
            self.write_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_json"})
            return
        if not isinstance(body, dict):
            self.write_json(HTTPStatus.BAD_REQUEST, {"error": "expected_json_object"})
            return

        event = str(body.get("event") or "")
        if not event:
            self.write_json(HTTPStatus.BAD_REQUEST, {"error": "missing_event"})
            return

        payload = body.get("payload") if isinstance(body.get("payload"), dict) else None
        if payload is None:
            meeting = body.get("meeting") if isinstance(body.get("meeting"), dict) else {}
            if not meeting:
                meeting = {
                    "id": str(body.get("meeting_id") or body.get("id") or "stub-meeting"),
                    "topic": str(body.get("topic") or "Stub meeting"),
                }
            payload = {"object": meeting}

        state = self.server.light.update_from_zoom_event(event, payload)
        if body.get("refresh_schedule", True):
            try:
                self.server.schedule.run_once()
                state = self.server.light.snapshot()
            except Exception as exc:
                print(f"Test schedule refresh after {event} failed: {exc}", flush=True)

        self.write_json(HTTPStatus.OK, {"ok": True, "state": state})

    def handle_url_validation(self, payload: dict[str, Any], secret_token: str) -> None:
        plain_token = str(payload.get("plainToken") or "")
        if not plain_token:
            self.write_json(HTTPStatus.BAD_REQUEST, {"error": "missing_plain_token"})
            return
        if not secret_token:
            self.write_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "ZOOM_WEBHOOK_SECRET_TOKEN is required for url validation"},
            )
            return

        self.write_json(
            HTTPStatus.OK,
            {
                "plainToken": plain_token,
                "encryptedToken": encrypted_validation_token(plain_token, secret_token),
            },
        )

    def stream_events(self) -> None:
        subscriber = self.server.light.subscribe()
        self.send_response(HTTPStatus.OK.value)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        try:
            while True:
                try:
                    message = subscriber.get(timeout=15)
                    self.write_sse(message)
                except queue.Empty:
                    self.write_sse("{}", event="ping")
        except (BrokenPipeError, ConnectionResetError):
            return
        finally:
            self.server.light.unsubscribe(subscriber)

    def write_sse(self, data: str, event: str | None = None) -> None:
        if event:
            self.wfile.write(f"event: {event}\n".encode("utf-8"))
        self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
        self.wfile.flush()

    def write_html(self, status: HTTPStatus, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def write_json(self, status: HTTPStatus, body: dict[str, Any]) -> None:
        encoded = json.dumps(body, indent=2).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def read_json_body(self) -> tuple[bytes, dict[str, Any] | None]:
        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length)
        if not raw_body:
            return raw_body, {}

        try:
            parsed = json.loads(raw_body)
        except json.JSONDecodeError:
            return raw_body, None
        return raw_body, parsed
