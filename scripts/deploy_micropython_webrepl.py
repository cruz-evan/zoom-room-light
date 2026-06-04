#!/usr/bin/env python3
"""Deploy MicroPython files to a Pico W over WebREPL, then soft-reset it."""

from __future__ import annotations

import argparse
import base64
import os
import socket
import struct
import sys
import time
from pathlib import Path

DEFAULT_FILES = (
    "boot.py",
    "main.py",
    "config.py",
    "light_output.py",
    "priority.py",
    "state_client.py",
    "wifi_connect.py",
)

WEBREPL_REQ = "<2sBBQLH64s"
WEBREPL_RESP = "<2sH"
WEBREPL_PUT_FILE = 1

OPCODE_TEXT = 0x1
OPCODE_BINARY = 0x2
OPCODE_CLOSE = 0x8
OPCODE_PING = 0x9
OPCODE_PONG = 0xA


class WebReplError(RuntimeError):
    pass


class WebReplClient:
    def __init__(self, host: str, password: str, port: int = 8266, timeout: float = 10.0):
        self.host = host
        self.password = password
        self.port = port
        self.timeout = timeout
        self.sock: socket.socket | None = None

    def __enter__(self) -> "WebReplClient":
        self.connect()
        self.login()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def connect(self) -> None:
        sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        sock.settimeout(self.timeout)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            "GET / HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        sock.sendall(request.encode("ascii"))
        response = self._recv_http_response(sock)
        status_line = response.split(b"\r\n", 1)[0]
        if b" 101 " not in status_line:
            raise WebReplError("WebSocket upgrade failed: " + status_line.decode("ascii", "replace"))
        self.sock = sock

    def login(self) -> None:
        prompt = self._read_until((b"Password:",), timeout=self.timeout)
        if b"Password:" not in prompt:
            raise WebReplError("WebREPL password prompt not received")

        self._send_frame(OPCODE_TEXT, (self.password + "\r").encode("utf-8"))
        response = self._read_until(
            (b"WebREPL connected", b">>>", b"Access denied", b"Invalid password"),
            timeout=self.timeout,
        )
        if b"Access denied" in response or b"Invalid password" in response:
            raise WebReplError("WebREPL password rejected")
        if b"WebREPL connected" not in response and b">>>" not in response:
            raise WebReplError("WebREPL login confirmation not received")

    def put_file(self, source: Path, remote_name: str) -> None:
        data_size = source.stat().st_size
        remote_bytes = remote_name.encode("utf-8")
        if len(remote_bytes) > 64:
            raise WebReplError(f"Remote path is too long for WebREPL: {remote_name}")

        request = struct.pack(
            WEBREPL_REQ,
            b"WA",
            WEBREPL_PUT_FILE,
            0,
            0,
            data_size,
            len(remote_bytes),
            remote_bytes.ljust(64, b"\x00"),
        )
        self._send_frame(OPCODE_BINARY, request)
        self._expect_webrepl_ok(f"starting upload of {remote_name}")

        with source.open("rb") as file_obj:
            while True:
                chunk = file_obj.read(1024)
                if not chunk:
                    break
                self._send_frame(OPCODE_BINARY, chunk)
                self._expect_webrepl_ok(f"uploading {remote_name}")

    def soft_reset(self) -> None:
        # Ctrl-C stops a running main loop; Ctrl-D triggers MicroPython soft reset.
        self._send_frame(OPCODE_TEXT, b"\x03")
        time.sleep(0.2)
        self._send_frame(OPCODE_TEXT, b"\x03")
        time.sleep(0.2)
        self._send_frame(OPCODE_TEXT, b"\x04")
        time.sleep(0.5)

    def close(self) -> None:
        if self.sock is None:
            return
        try:
            self._send_frame(OPCODE_CLOSE, b"")
        except OSError:
            pass
        try:
            self.sock.close()
        finally:
            self.sock = None

    def _expect_webrepl_ok(self, action: str) -> None:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            assert self.sock is not None
            self.sock.settimeout(max(0.1, deadline - time.monotonic()))
            opcode, payload = self._recv_frame()
            if opcode == OPCODE_CLOSE:
                raise WebReplError(f"Connection closed while {action}")
            if opcode == OPCODE_PING:
                self._send_frame(OPCODE_PONG, payload)
                continue
            if opcode == OPCODE_TEXT:
                continue
            if len(payload) != struct.calcsize(WEBREPL_RESP):
                raise WebReplError(f"Unexpected WebREPL response while {action}: {payload!r}")
            sig, code = struct.unpack(WEBREPL_RESP, payload)
            if sig != b"WB" or code != 0:
                raise WebReplError(f"WebREPL error while {action}: signature={sig!r} code={code}")
            return
        raise WebReplError(f"Timed out waiting for WebREPL response while {action}")

    def _read_until(self, needles: tuple[bytes, ...], timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        data = bytearray()
        while time.monotonic() < deadline:
            remaining = max(0.1, deadline - time.monotonic())
            assert self.sock is not None
            self.sock.settimeout(remaining)
            try:
                opcode, payload = self._recv_frame()
            except socket.timeout:
                break
            if opcode == OPCODE_CLOSE:
                break
            if opcode == OPCODE_PING:
                self._send_frame(OPCODE_PONG, payload)
                continue
            data.extend(payload)
            if any(needle in data for needle in needles):
                return bytes(data)
        return bytes(data)

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        assert self.sock is not None
        first = 0x80 | opcode
        length = len(payload)
        if length < 126:
            header = struct.pack("!BB", first, 0x80 | length)
        elif length < 65536:
            header = struct.pack("!BBH", first, 0x80 | 126, length)
        else:
            header = struct.pack("!BBQ", first, 0x80 | 127, length)
        mask = os.urandom(4)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self.sock.sendall(header + mask + masked)

    def _recv_frame(self) -> tuple[int, bytes]:
        assert self.sock is not None
        header = self._recv_exact(2)
        first, second = header
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._recv_exact(8))[0]
        mask = self._recv_exact(4) if masked else b""
        payload = self._recv_exact(length) if length else b""
        if masked:
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        return opcode, payload

    def _recv_exact(self, size: int) -> bytes:
        assert self.sock is not None
        chunks = bytearray()
        while len(chunks) < size:
            chunk = self.sock.recv(size - len(chunks))
            if not chunk:
                raise WebReplError("WebREPL connection closed unexpectedly")
            chunks.extend(chunk)
        return bytes(chunks)

    @staticmethod
    def _recv_http_response(sock: socket.socket) -> bytes:
        response = bytearray()
        while b"\r\n\r\n" not in response:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response.extend(chunk)
            if len(response) > 65536:
                raise WebReplError("WebSocket upgrade response too large")
        return bytes(response)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Upload micropython_light files to a Pico W over WebREPL and soft-reset it."
    )
    parser.add_argument("host", help="Pico W IP address or hostname")
    parser.add_argument(
        "--password",
        default=os.environ.get("WEBREPL_PASSWORD"),
        help="WebREPL password. Defaults to WEBREPL_PASSWORD env var.",
    )
    parser.add_argument("--port", type=int, default=8266, help="WebREPL port. Defaults to 8266.")
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=Path("micropython_light"),
        help="Local MicroPython source directory. Defaults to micropython_light.",
    )
    parser.add_argument(
        "--file",
        action="append",
        dest="files",
        help="File to upload from source-dir. May be passed multiple times. Defaults to app files.",
    )
    parser.add_argument("--no-reset", action="store_true", help="Upload files without soft-resetting afterward.")
    parser.add_argument("--timeout", type=float, default=10.0, help="Socket timeout in seconds.")
    return parser.parse_args(argv)


def resolve_files(source_dir: Path, requested_files: list[str] | None) -> list[tuple[Path, str]]:
    names = requested_files or list(DEFAULT_FILES)
    resolved: list[tuple[Path, str]] = []
    missing: list[str] = []
    for name in names:
        source = source_dir / name
        if source.exists():
            resolved.append((source, "/" + Path(name).name))
        elif requested_files:
            missing.append(str(source))
        elif name == "config.py":
            print(f"Skipping {source}; create it from config.example.py to deploy secrets.")
        else:
            missing.append(str(source))
    if missing:
        raise SystemExit("Missing deploy file(s): " + ", ".join(missing))
    return resolved


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if not args.password:
        raise SystemExit("Set WEBREPL_PASSWORD or pass --password.")

    files = resolve_files(args.source_dir, args.files)
    if not files:
        raise SystemExit("No files to upload.")

    with WebReplClient(args.host, args.password, port=args.port, timeout=args.timeout) as client:
        for source, remote in files:
            print(f"Uploading {source} -> {args.host}:{remote}")
            client.put_file(source, remote)

    if not args.no_reset:
        print("Soft resetting MicroPython via WebREPL")
        with WebReplClient(args.host, args.password, port=args.port, timeout=args.timeout) as client:
            client.soft_reset()

    print("Deploy complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
