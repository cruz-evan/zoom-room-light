from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ServerConfig:
    host: str
    port: int
    webhook_secret_token: str
    schedule_user_id: str
    schedule_poll_seconds: int
    schedule_lookahead_minutes: int
    ending_soon_minutes: int
    serial_enabled: bool
    serial_port: str
    serial_baud: int
    serial_timeout_seconds: float
    serial_settle_seconds: float
    serial_reconnect_seconds: float
    serial_dry_run: bool
    device_token: str
    device_poll_seconds: int


def load_dotenv(path: str = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def load_config(host: str | None = None, port: int | None = None) -> ServerConfig:
    load_dotenv()
    return ServerConfig(
        host=host or os.getenv("HOST", "0.0.0.0"),
        port=port or int(os.getenv("PORT", "5000")),
        webhook_secret_token=os.getenv("ZOOM_WEBHOOK_SECRET_TOKEN", ""),
        schedule_user_id=os.getenv("ZOOM_SCHEDULE_USER_ID") or os.getenv("ZOOM_USER_ID", "me"),
        schedule_poll_seconds=int(os.getenv("SCHEDULE_POLL_SECONDS", "60")),
        schedule_lookahead_minutes=int(os.getenv("SCHEDULE_LOOKAHEAD_MINUTES", "5")),
        ending_soon_minutes=int(os.getenv("ENDING_SOON_MINUTES", "5")),
        serial_enabled=env_bool("RP2_SERIAL_ENABLED", False),
        serial_port=os.getenv("RP2_SERIAL_PORT") or os.getenv("RP2_PORT", "auto"),
        serial_baud=int(os.getenv("RP2_SERIAL_BAUD") or os.getenv("RP2_BAUD", "115200")),
        serial_timeout_seconds=float(os.getenv("RP2_SERIAL_TIMEOUT_SECONDS", "1")),
        serial_settle_seconds=float(os.getenv("RP2_SERIAL_SETTLE_SECONDS", "1.2")),
        serial_reconnect_seconds=float(os.getenv("RP2_SERIAL_RECONNECT_SECONDS", "5")),
        serial_dry_run=env_bool("RP2_SERIAL_DRY_RUN", False),
        device_token=os.getenv("DEVICE_TOKEN", ""),
        device_poll_seconds=int(os.getenv("DEVICE_POLL_SECONDS", "60")),
    )
