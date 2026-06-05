import json

from device.telemetry import UdpTelemetry, from_config


class FakeSocket:
    def __init__(self):
        self.sent = []

    def setsockopt(self, *args):
        return None

    def sendto(self, data, address):
        self.sent.append((data, address))


def test_telemetry_payload_includes_device_id():
    telemetry = UdpTelemetry(enabled=True, host="127.0.0.1", port=9977, device_id="board-room-a")
    fake_socket = FakeSocket()
    telemetry.sock = fake_socket

    telemetry.log("boot", state_poll_seconds=5)

    data, address = fake_socket.sent[0]
    payload = json.loads(data.decode("utf-8"))

    assert address == ("127.0.0.1", 9977)
    assert payload["device"] == "board-room-a"
    assert payload["device_id"] == "board-room-a"
    assert payload["event"] == "boot"
    assert payload["state_poll_seconds"] == 5


def test_resource_monitor_config_enables_udp_transport():
    class Config:
        TELEMETRY_ENABLED = False
        RESOURCE_MONITOR_ENABLED = True
        TELEMETRY_HOST = "127.0.0.1"
        TELEMETRY_PORT = 9977
        TELEMETRY_DEVICE_ID = "board-room-a"

    telemetry = from_config(Config)

    assert telemetry.enabled is True
