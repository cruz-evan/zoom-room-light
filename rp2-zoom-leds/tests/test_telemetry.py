import json

from device.telemetry import UdpTelemetry


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
