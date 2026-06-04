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


def load_config(host: str | None = None, port: int | None = None) -> ServerConfig:
    load_dotenv()
    return ServerConfig(
        host=host or os.getenv("HOST", "0.0.0.0"),
        port=port or int(os.getenv("PORT", "5000")),
        webhook_secret_token=os.getenv("ZOOM_WEBHOOK_SECRET_TOKEN", ""),
        schedule_user_id=os.getenv("ZOOM_SCHEDULE_USER_ID") or os.getenv("ZOOM_USER_ID", "me"),
        schedule_poll_seconds=int(os.getenv("SCHEDULE_POLL_SECONDS", "60")),
        schedule_lookahead_minutes=int(os.getenv("SCHEDULE_LOOKAHEAD_MINUTES", "15")),
    )
