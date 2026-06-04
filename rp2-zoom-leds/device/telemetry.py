try:
    import usocket as socket
except ImportError:
    import socket

try:
    import ujson as json
except ImportError:
    import json

import time

try:
    import _thread
except ImportError:
    _thread = None


DEFAULT_HOST = "255.255.255.255"
DEFAULT_PORT = 9977


def _ticks_ms():
    if hasattr(time, "ticks_ms"):
        return time.ticks_ms()
    return int(time.monotonic() * 1000)


class UdpTelemetry:
    def __init__(self, enabled=False, host=DEFAULT_HOST, port=DEFAULT_PORT, device_id="pico-w"):
        self.enabled = bool(enabled and host and port)
        self.host = host
        self.port = int(port)
        self.device_id = device_id or "pico-w"
        self.sequence = 0
        self.sock = None
        self.lock = _thread.allocate_lock() if _thread else None

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

    def log(self, event, **fields):
        if not self.enabled:
            return

        if self.lock is None:
            return self._log_unlocked(event, **fields)

        self.lock.acquire()
        try:
            return self._log_unlocked(event, **fields)
        finally:
            self.lock.release()

    def _log_unlocked(self, event, **fields):
        self.sequence += 1
        payload = {
            "t_ms": _ticks_ms(),
            "seq": self.sequence,
            "device": self.device_id,
            "event": event,
        }
        payload.update(fields)

        try:
            data = json.dumps(payload).encode("utf-8")
            self._socket().sendto(data, (self.host, self.port))
        except Exception:
            self.close()

    def _socket(self):
        if self.sock is None:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            except Exception:
                pass
        return self.sock


def from_config(config):
    return UdpTelemetry(
        enabled=getattr(config, "TELEMETRY_ENABLED", False),
        host=getattr(config, "TELEMETRY_HOST", DEFAULT_HOST),
        port=getattr(config, "TELEMETRY_PORT", DEFAULT_PORT),
        device_id=getattr(config, "TELEMETRY_DEVICE_ID", "pico-w"),
    )
