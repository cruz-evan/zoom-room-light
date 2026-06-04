from __future__ import annotations

import glob
import json
import threading
import time
from dataclasses import dataclass
from typing import Any

try:
    import serial
    from serial.tools import list_ports
except ImportError:  # pragma: no cover - exercised when pyserial is not installed.
    serial = None
    list_ports = None

from .config import ServerConfig


DEFAULT_BAUD = 115200


@dataclass(frozen=True)
class SerialLightConfig:
    enabled: bool
    port: str = "auto"
    baud: int = DEFAULT_BAUD
    timeout_seconds: float = 1.0
    settle_seconds: float = 1.2
    reconnect_seconds: float = 5.0
    dry_run: bool = False


def list_candidate_ports() -> list[str]:
    scored: list[tuple[int, str]] = []

    if list_ports is not None:
        for info in list_ports.comports():
            fields = (
                info.device,
                info.description,
                info.manufacturer,
                getattr(info, "product", ""),
            )
            haystack = " ".join(str(field or "") for field in fields).lower()

            score = 0
            for keyword in ("pico", "rp2", "rp2040", "rp2350", "micropython", "raspberry"):
                if keyword in haystack:
                    score += 5
            if info.device.startswith("/dev/cu.usbmodem"):
                score += 4
            if info.device.startswith("/dev/tty.usbmodem"):
                score += 2

            if score:
                scored.append((score, info.device))

    if not scored:
        for path in glob.glob("/dev/cu.usbmodem*") + glob.glob("/dev/tty.usbmodem*"):
            scored.append((1, path))

    return [device for _, device in sorted(scored, key=lambda item: (-item[0], item[1]))]


def resolve_port(port: str) -> str:
    if port and port != "auto":
        return port

    candidates = list_candidate_ports()
    if not candidates:
        raise RuntimeError(
            "No candidate RP2 USB serial port found. Set RP2_SERIAL_PORT=/dev/cu.usbmodemXXXX."
        )
    return candidates[0]


def command_from_state(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("in_use"):
        minutes_until_end = state.get("minutes_until_end")
        if minutes_until_end is not None:
            return {
                "mode": "meeting_status",
                "state": "ending_soon",
                "minutes": _minutes(minutes_until_end),
            }
        return {"mode": "meeting_status", "state": "in_progress"}

    if state.get("next_meeting_id"):
        return {
            "mode": "meeting_status",
            "state": "starting_soon",
            "minutes": _minutes(state.get("minutes_until_next")),
        }

    return {"mode": "off"}


def encode_command(command: dict[str, Any]) -> str:
    return json.dumps(command, separators=(",", ":")) + "\n"


def _minutes(value: Any) -> int:
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        minutes = 5
    return max(0, min(120, minutes))


class SerialLightOutput:
    def __init__(self, config: SerialLightConfig) -> None:
        self._config = config
        self._lock = threading.Lock()
        self._connection: Any | None = None
        self._resolved_port: str | None = None
        self._last_command: dict[str, Any] | None = None
        self._next_connect_attempt = 0.0

    @classmethod
    def from_server_config(cls, config: ServerConfig) -> "SerialLightOutput | None":
        if not config.serial_enabled:
            return None
        return cls(
            SerialLightConfig(
                enabled=config.serial_enabled,
                port=config.serial_port,
                baud=config.serial_baud,
                timeout_seconds=config.serial_timeout_seconds,
                settle_seconds=config.serial_settle_seconds,
                reconnect_seconds=config.serial_reconnect_seconds,
                dry_run=config.serial_dry_run,
            )
        )

    def publish(self, state: dict[str, Any]) -> None:
        command = command_from_state(state)
        if command == self._last_command:
            return

        line = encode_command(command)
        if self._config.dry_run:
            print(f"[serial dry-run] {line.strip()}", flush=True)
            self._last_command = command
            return

        with self._lock:
            try:
                self._ensure_connection()
                if self._connection is None:
                    return
                self._connection.write(line.encode("utf-8"))
                self._connection.flush()
                self._last_command = command
                print(f"[serial] sent to {self._resolved_port}: {line.strip()}", flush=True)
            except Exception as exc:
                print(f"[serial] output unavailable: {exc}", flush=True)
                self._close_locked()
                self._next_connect_attempt = time.monotonic() + self._config.reconnect_seconds

    def close(self) -> None:
        with self._lock:
            self._close_locked()

    def _ensure_connection(self) -> None:
        if self._connection is not None:
            return
        if serial is None:
            raise RuntimeError("pyserial is not installed. Run: python3 -m pip install -r requirements.txt")

        now = time.monotonic()
        if now < self._next_connect_attempt:
            return

        self._resolved_port = resolve_port(self._config.port)
        self._connection = serial.Serial(
            self._resolved_port,
            self._config.baud,
            timeout=self._config.timeout_seconds,
            write_timeout=self._config.timeout_seconds,
        )
        time.sleep(self._config.settle_seconds)
        print(f"[serial] connected to {self._resolved_port} at {self._config.baud}", flush=True)

    def _close_locked(self) -> None:
        if self._connection is not None:
            try:
                self._connection.close()
            finally:
                self._connection = None
