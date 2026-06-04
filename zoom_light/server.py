from __future__ import annotations

import json
import queue
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from .config import ServerConfig
from .dashboard import DASHBOARD_HTML
from .light_state import LightController
from .schedule import ScheduleWatcher
from .security import encrypted_validation_token, verify_zoom_signature
from .serial_output import SerialLightOutput, command_from_state


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
        path = urlparse(self.path).path

        if path == "/":
            self.write_html(HTTPStatus.OK, DASHBOARD_HTML)
            return

        if path == "/state":
            self.write_json(HTTPStatus.OK, self.server.light.snapshot())
            return

        if path == "/device/state":
            if not self.authorize_device():
                self.write_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                return
            snapshot = self.server.light.snapshot()
            command = command_from_state(snapshot)
            self.write_json(
                HTTPStatus.OK,
                {
                    "v": 1,
                    "command": command,
                    "poll_seconds": 5,
                    "updated_at": snapshot.get("updated_at", ""),
                    "last_event": snapshot.get("last_event", ""),
                },
            )
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

    def do_POST(self) -> None:
        path = urlparse(self.path).path
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
