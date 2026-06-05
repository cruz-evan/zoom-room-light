import importlib
import sys
import time
import types


class FakePin:
    OUT = 1

    def __init__(self, pin, mode):
        self.pin = pin
        self.mode = mode
        self.last_value = 0

    def value(self, value):
        self.last_value = value


class FakeNeoPixel:
    def __init__(self, pin, count):
        self.pin = pin
        self.values = [(0, 0, 0)] * count
        self.write_count = 0

    def __setitem__(self, index, value):
        self.values[index] = value

    def write(self):
        self.write_count += 1


def load_led_strip(monkeypatch, ticks):
    monkeypatch.setitem(sys.modules, "machine", types.SimpleNamespace(Pin=FakePin))
    monkeypatch.setitem(sys.modules, "neopixel", types.SimpleNamespace(NeoPixel=FakeNeoPixel))
    monkeypatch.setattr(time, "ticks_ms", lambda: ticks["now"], raising=False)
    monkeypatch.setattr(time, "ticks_diff", lambda now, start: now - start, raising=False)
    sys.modules.pop("device.led_strip", None)
    return importlib.import_module("device.led_strip")


def test_starting_soon_renders_warm_chase(monkeypatch):
    ticks = {"now": 350}
    led_strip = load_led_strip(monkeypatch, ticks)
    strip = led_strip.LedStrip(pin=0, count=24, brightness=1.0)

    strip.apply({"mode": "meeting_status", "state": "starting_soon", "minutes": 5})

    pixels = strip.pixels.values
    assert len(set(pixels)) > 2
    assert any(red > 220 and green > 180 and blue < 60 for red, green, blue in pixels)
    assert any(blue > red and blue > green for red, green, blue in pixels)


def test_in_progress_renders_steady_red(monkeypatch):
    ticks = {"now": 0}
    led_strip = load_led_strip(monkeypatch, ticks)
    strip = led_strip.LedStrip(pin=0, count=24, brightness=1.0)

    strip.apply({"mode": "meeting_status", "state": "in_progress"})

    pixels = strip.pixels.values
    assert len(set(pixels)) == 1
    red, green, blue = pixels[0]
    assert red > 200
    assert green == 0
    assert 0 < blue < 30
