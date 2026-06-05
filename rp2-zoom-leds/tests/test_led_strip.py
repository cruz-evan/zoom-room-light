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


def test_starting_soon_renders_cyan_block_with_tail(monkeypatch):
    ticks = {"now": 350}
    led_strip = load_led_strip(monkeypatch, ticks)
    strip = led_strip.LedStrip(pin=0, count=24, brightness=1.0)

    strip.apply({"mode": "meeting_status", "state": "starting_soon", "minutes": 5})

    pixels = strip.pixels.values
    cyan = (44, 213, 252)
    background = (0, 2, 8)
    full_block_indexes = list(range(4, 11))
    tail_indexes = [3, 2, 1, 0, 23, 22, 21]

    assert [index for index, pixel in enumerate(pixels) if pixel == cyan] == full_block_indexes
    assert all(pixels[index] != background for index in tail_indexes)
    assert all(pixels[index] != cyan for index in tail_indexes)
    assert all(
        sum(pixels[tail_indexes[index]]) > sum(pixels[tail_indexes[index + 1]])
        for index in range(len(tail_indexes) - 1)
    )
    assert all(
        pixel == background
        for index, pixel in enumerate(pixels)
        if index not in set(full_block_indexes + tail_indexes)
    )


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
