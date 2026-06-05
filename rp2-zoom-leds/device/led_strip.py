import math
import time

from machine import Pin
import neopixel


def _ticks_ms():
    return time.ticks_ms()


def _ticks_diff(now, start):
    return time.ticks_diff(now, start)


class StatusLed:
    def __init__(self):
        self.led = None
        self.state = 0
        self.last_toggle = _ticks_ms()

        for pin_id in ("LED", 25):
            try:
                self.led = Pin(pin_id, Pin.OUT)
                break
            except Exception:
                self.led = None

    def set(self, value):
        if self.led is None:
            return
        self.state = 1 if value else 0
        self.led.value(self.state)

    def toggle(self):
        self.set(0 if self.state else 1)

    def tick_heartbeat(self, interval_ms=500):
        now = _ticks_ms()
        if _ticks_diff(now, self.last_toggle) >= interval_ms:
            self.last_toggle = now
            self.toggle()


def _frame_interval_ms(max_refresh_fps):
    try:
        fps = int(max_refresh_fps)
    except (TypeError, ValueError):
        fps = 30
    if fps <= 0:
        return 0
    return max(1, (1000 + fps - 1) // fps)


class LedStrip:
    def __init__(self, pin, count, brightness=0.35, max_refresh_fps=30):
        self.count = int(count)
        self.brightness = max(0.0, min(float(brightness), 1.0))
        self.frame_interval_ms = _frame_interval_ms(max_refresh_fps)
        self.last_refresh_ms = None
        self.pixels = None
        self.available = False
        self.current_command = {"mode": "off"}
        self.pulse_started = _ticks_ms()
        self.effect_started = _ticks_ms()

        try:
            self.pixels = neopixel.NeoPixel(Pin(pin, Pin.OUT), self.count)
            self.available = True
            self.off(force=True)
        except Exception:
            self.pixels = None
            self.available = False

    def _refresh_due(self, force=False):
        now = _ticks_ms()
        if force or self.frame_interval_ms <= 0 or self.last_refresh_ms is None:
            return True, now
        return _ticks_diff(now, self.last_refresh_ms) >= self.frame_interval_ms, now

    def _scaled(self, rgb, multiplier=1.0):
        scale = self.brightness * multiplier
        return tuple(int(max(0, min(255, value)) * scale) for value in rgb)

    def _write_color(self, rgb, multiplier=1.0, force=False):
        if not self.available or self.pixels is None:
            return False

        due, now = self._refresh_due(force)
        if not due:
            return True

        try:
            color = self._scaled(rgb, multiplier)
            for index in range(self.count):
                self.pixels[index] = color
            self.pixels.write()
            self.last_refresh_ms = now
            return True
        except Exception:
            self.available = False
            self.pixels = None
            return False

    def off(self, force=True):
        return self._write_color((0, 0, 0), 1.0, force=force)

    def apply(self, command):
        self.current_command = command
        mode = command.get("mode")

        if mode == "off":
            return self.off(force=True)

        if mode == "solid":
            return self._write_color(command["rgb"], force=True)

        if mode == "pulse":
            self.pulse_started = _ticks_ms()
            return self._render_pulse(force=True)

        if mode == "meeting_status":
            self.effect_started = _ticks_ms()
            return self._render_meeting_status(force=True)

        if mode == "meeting":
            if not command.get("active"):
                return self.off(force=True)
            return self._write_color(
                self._meeting_rgb(command.get("participants", 0)),
                force=True,
            )

        return False

    def tick(self):
        mode = self.current_command.get("mode")
        if mode == "pulse":
            self._render_pulse()
        elif mode == "meeting_status":
            self._render_meeting_status()

    def _render_pulse(self, force=False):
        command = self.current_command
        rgb = command.get("rgb", (0, 120, 255))
        speed = float(command.get("speed", 0.6))
        cycle_ms = max(200, int(1000 / speed))
        elapsed = _ticks_diff(_ticks_ms(), self.pulse_started) % cycle_ms
        half_cycle = max(1, cycle_ms // 2)

        if elapsed <= half_cycle:
            level = elapsed / half_cycle
        else:
            level = (cycle_ms - elapsed) / half_cycle

        return self._write_color(rgb, 0.08 + (0.92 * level), force=force)

    def _meeting_rgb(self, participants):
        if participants <= 1:
            return (0, 120, 255)
        if participants <= 4:
            return (0, 190, 100)
        return (255, 140, 0)

    def _write_pattern(self, colors, force=False):
        if not self.available or self.pixels is None:
            return False

        due, now = self._refresh_due(force)
        if not due:
            return True

        try:
            for index in range(self.count):
                self.pixels[index] = self._scaled(colors(index))
            self.pixels.write()
            self.last_refresh_ms = now
            return True
        except Exception:
            self.available = False
            self.pixels = None
            return False

    def _triangle_wave(self, cycle_ms):
        elapsed = _ticks_diff(_ticks_ms(), self.effect_started) % cycle_ms
        half_cycle = max(1, cycle_ms // 2)
        if elapsed <= half_cycle:
            return elapsed / half_cycle
        return (cycle_ms - elapsed) / half_cycle

    def _sine_wave(self, cycle_ms):
        elapsed = _ticks_diff(_ticks_ms(), self.effect_started) % cycle_ms
        phase = (elapsed / cycle_ms) * 6.283185307179586
        return 0.5 - (0.5 * math.cos(phase))

    def _render_meeting_status(self, force=False):
        state = self.current_command.get("state")
        if state == "starting_soon":
            return self._render_starting_soon(force=force)
        if state == "in_progress":
            return self._render_in_progress(force=force)
        if state == "ending_soon":
            return self._render_ending_soon(force=force)
        return self.off(force=force)

    def _render_starting_soon(self, force=False):
        now = _ticks_ms()
        head = (now // 35) % max(1, self.count)
        block = 7
        tail = 7
        cyan = (44, 213, 252)
        background = (0, 2, 8)

        def color_at(index):
            distance = (head - index) % self.count
            if distance < block:
                return cyan
            if distance < block + tail:
                level = 1.0 - ((distance - block + 1) / (tail + 1))
                return (
                    int(background[0] + ((cyan[0] - background[0]) * level)),
                    int(background[1] + ((cyan[1] - background[1]) * level)),
                    int(background[2] + ((cyan[2] - background[2]) * level)),
                )
            return background

        return self._write_pattern(color_at, force=force)

    def _render_in_progress(self, force=False):
        level = 0.25 + (0.75 * self._sine_wave(6200))
        return self._write_color((43, 82, 252), level, force=force)

    def _render_ending_soon(self, force=False):
        minutes = float(self.current_command.get("minutes", 5.0))
        threshold = max(1.0, float(self.current_command.get("threshold", 5.0)))
        urgency = 1.0 - min(max(minutes / threshold, 0.0), 1.0)
        cycle_ms = int(1300 - (800 * urgency))
        level = 0.25 + (0.75 * self._triangle_wave(max(350, cycle_ms)))
        red = 255
        green = int(150 - (105 * urgency))

        return self._write_color((red, green, 0), level, force=force)
