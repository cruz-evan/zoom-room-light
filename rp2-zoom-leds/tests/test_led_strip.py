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


class FakeBufferedNeoPixel:
    ORDER = (1, 0, 2)

    def __init__(self, pin, count):
        self.pin = pin
        self.buf = bytearray(count * 3)
        self.set_count = 0
        self.write_count = 0

    def __setitem__(self, index, value):
        self.set_count += 1
        offset = index * 3
        self.buf[offset] = value[1]
        self.buf[offset + 1] = value[0]
        self.buf[offset + 2] = value[2]

    def write(self):
        self.write_count += 1


def load_led_strip(monkeypatch, ticks, pixel_class=FakeNeoPixel):
    monkeypatch.setitem(sys.modules, "machine", types.SimpleNamespace(Pin=FakePin))
    monkeypatch.setitem(sys.modules, "neopixel", types.SimpleNamespace(NeoPixel=pixel_class))
    monkeypatch.setattr(time, "ticks_ms", lambda: ticks["now"], raising=False)
    monkeypatch.setattr(time, "ticks_diff", lambda now, start: now - start, raising=False)
    sys.modules.pop("device.led_strip", None)
    return importlib.import_module("device.led_strip")


def test_starting_soon_pulses_full_strip_cyan(monkeypatch):
    ticks = {"now": 0}
    led_strip = load_led_strip(monkeypatch, ticks)
    strip = led_strip.LedStrip(pin=0, count=24, brightness=1.0)

    strip.apply({"mode": "meeting_status", "state": "starting_soon", "minutes": 5})

    dim_pixels = strip.pixels.values
    assert len(set(dim_pixels)) == 1
    assert dim_pixels[0] == (11, 53, 63)

    ticks["now"] = 900
    strip.tick()

    peak_pixels = strip.pixels.values
    assert len(set(peak_pixels)) == 1
    assert peak_pixels[0] == (44, 213, 252)


def test_starting_soon_blends_in_progress_blue_as_start_approaches(monkeypatch):
    ticks = {"now": 0}
    led_strip = load_led_strip(monkeypatch, ticks)
    cyan = (11, 53, 63)
    blue = (11, 21, 63)

    strip = led_strip.LedStrip(pin=0, count=24, brightness=1.0)
    strip.apply(
        {
            "mode": "meeting_status",
            "state": "starting_soon",
            "minutes": 15,
            "threshold": 15,
            "seconds_until_expected_state_change": 900,
        }
    )
    assert set(strip.pixels.values) == {cyan}
    assert list(strip.pixels.values).count(blue) == 0

    strip.apply(
        {
            "mode": "meeting_status",
            "state": "starting_soon",
            "minutes": 15,
            "threshold": 15,
            "seconds_until_expected_state_change": 450,
        }
    )
    assert set(strip.pixels.values) == {cyan, blue}
    halfway_blue = list(strip.pixels.values).count(blue)
    assert 8 <= halfway_blue <= 16

    strip.apply(
        {
            "mode": "meeting_status",
            "state": "starting_soon",
            "minutes": 15,
            "threshold": 15,
            "seconds_until_expected_state_change": 0,
        }
    )
    assert set(strip.pixels.values) == {blue}
    assert list(strip.pixels.values).count(blue) == 24


def test_in_progress_renders_full_strip_blue_pulse(monkeypatch):
    ticks = {"now": 0}
    led_strip = load_led_strip(monkeypatch, ticks)
    strip = led_strip.LedStrip(pin=0, count=24, brightness=1.0)

    strip.apply({"mode": "meeting_status", "state": "in_progress"})

    dim_pixels = list(strip.pixels.values)
    assert len(set(dim_pixels)) == 1
    assert dim_pixels[0] == (11, 21, 63)

    ticks["now"] = 646
    strip.tick()

    early_pixels = list(strip.pixels.values)
    assert len(set(early_pixels)) == 1
    assert early_pixels[0] == (16, 30, 91)

    ticks["now"] = 1292
    strip.tick()

    midpoint_pixels = list(strip.pixels.values)
    assert len(set(midpoint_pixels)) == 1
    assert midpoint_pixels[0] == (27, 51, 157)

    ticks["now"] = 2583
    strip.tick()

    peak_pixels = strip.pixels.values
    assert len(set(peak_pixels)) == 1
    assert peak_pixels[0] == (43, 82, 252)


def test_animation_ticks_are_limited_to_max_refresh_rate(monkeypatch):
    ticks = {"now": 0}
    led_strip = load_led_strip(monkeypatch, ticks)
    strip = led_strip.LedStrip(pin=0, count=24, brightness=1.0, max_refresh_fps=30)

    strip.apply({"mode": "meeting_status", "state": "ending_soon", "minutes": 0, "threshold": 1})
    writes_after_apply = strip.pixels.write_count

    ticks["now"] = 33
    strip.tick()

    assert strip.pixels.write_count == writes_after_apply

    ticks["now"] = 34
    strip.tick()

    assert strip.pixels.write_count == writes_after_apply + 1


def test_unchanged_solid_animation_frames_are_not_rewritten(monkeypatch):
    ticks = {"now": 0}
    led_strip = load_led_strip(monkeypatch, ticks)
    strip = led_strip.LedStrip(pin=0, count=24, brightness=1.0, max_refresh_fps=30)

    strip.apply({"mode": "meeting_status", "state": "in_progress"})
    writes_after_apply = strip.pixels.write_count

    ticks["now"] = 34
    strip.tick()

    assert strip.pixels.write_count == writes_after_apply

    ticks["now"] = 646
    strip.tick()

    assert strip.pixels.write_count == writes_after_apply + 1


def test_full_strip_colors_use_raw_buffer_when_available(monkeypatch):
    ticks = {"now": 0}
    led_strip = load_led_strip(monkeypatch, ticks, FakeBufferedNeoPixel)
    strip = led_strip.LedStrip(pin=0, count=4, brightness=1.0)

    strip.apply({"mode": "solid", "rgb": [10, 20, 30]})

    assert strip.pixels.buf == bytearray([20, 10, 30] * 4)
    assert strip.pixels.set_count == 0


def test_starting_soon_uses_raw_buffer_for_countdown_pixels(monkeypatch):
    ticks = {"now": 0}
    led_strip = load_led_strip(monkeypatch, ticks, FakeBufferedNeoPixel)
    strip = led_strip.LedStrip(pin=0, count=24, brightness=1.0, max_refresh_fps=30)
    blend_calls = {"count": 0}
    encode_calls = {"count": 0}
    original_blend_pixel = strip._starting_soon_blend_pixel
    original_encoded_pixel = strip._encoded_pixel

    def counted_blend_pixel(index, frame, threshold):
        blend_calls["count"] += 1
        return original_blend_pixel(index, frame, threshold)

    def counted_encoded_pixel(color, bpp):
        encode_calls["count"] += 1
        return original_encoded_pixel(color, bpp)

    strip._starting_soon_blend_pixel = counted_blend_pixel
    strip._encoded_pixel = counted_encoded_pixel

    strip.apply(
        {
            "mode": "meeting_status",
            "state": "starting_soon",
            "minutes": 15,
            "threshold": 15,
            "seconds_until_expected_state_change": 450,
        }
    )
    ticks["now"] = 34
    strip.tick()

    assert strip.pixels.set_count == 0
    assert strip.pixels.write_count == 2
    assert blend_calls["count"] == 24
    assert encode_calls["count"] == 2

    ticks["now"] = 68
    strip.tick()

    assert strip.pixels.set_count == 0
    assert blend_calls["count"] == 24
    assert encode_calls["count"] == 4

    ticks["now"] = 102
    strip.tick()

    assert strip.pixels.set_count == 0
    assert blend_calls["count"] == 48
    assert encode_calls["count"] == 6


def test_apply_forces_new_command_without_waiting_for_next_frame(monkeypatch):
    ticks = {"now": 0}
    led_strip = load_led_strip(monkeypatch, ticks)
    strip = led_strip.LedStrip(pin=0, count=24, brightness=1.0, max_refresh_fps=30)

    strip.apply({"mode": "meeting_status", "state": "in_progress"})
    writes_after_first_command = strip.pixels.write_count

    ticks["now"] = 10
    strip.apply({"mode": "meeting_status", "state": "ending_soon", "minutes": 2})

    assert strip.pixels.write_count == writes_after_first_command + 1
