import sys
import types


class FakeNetwork(types.SimpleNamespace):
    STA_IF = 0

    class WLAN:
        PM_NONE = 0


sys.modules.setdefault("network", FakeNetwork())

from device.wifi_connect import _resolve_hostname


class FakeWlan:
    def __init__(self, mac=b"\xaa\xbb\xcc\xdd\xee\xff"):
        self.mac = mac

    def config(self, key=None, **kwargs):
        if key == "mac":
            return self.mac
        return None


def test_auto_hostname_uses_wifi_mac_suffix():
    assert _resolve_hostname(FakeWlan(), "auto", "zoom-light") == "zoom-light-ddeeff"


def test_explicit_hostname_wins():
    assert _resolve_hostname(FakeWlan(), "zoom-light-board-room-a", "zoom-light") == "zoom-light-board-room-a"


def test_hostname_template_can_include_mac_suffix():
    assert _resolve_hostname(FakeWlan(), "zoom-light-{mac}", "zoom-light") == "zoom-light-ddeeff"
