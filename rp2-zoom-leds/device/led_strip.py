import math
import time

from machine import Pin
import neopixel


TWO_PI = 6.283185307179586
IN_PROGRESS_PULSE_CYCLE_MS = 5166
STARTING_SOON_LED_MS = 55
STARTING_SOON_BLOCK_LEDS = 7.0
STARTING_SOON_TAIL_LEDS = 10.0
STARTING_SOON_CYAN = (44, 213, 252)
STARTING_SOON_BACKGROUND = (0, 2, 8)


def _ticks_ms():
    return time.ticks_ms()


def _ticks_diff(now, start):
    return time.ticks_diff(now, start)


def _clamp_unit(value):
    if value <= 0.0:
        return 0.0
    if value >= 1.0:
        return 1.0
    return value


def _smoothstep(value):
    value = _clamp_unit(value)
    return value * value * (3.0 - (2.0 * value))


def _mix_rgb(start, end, amount):
    amount = _clamp_unit(amount)
    return tuple(
        int(start[index] + ((end[index] - start[index]) * amount) + 0.5)
        for index in range(3)
    )


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
        self._last_solid_color = None
        self._starting_soon_active = ()

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
        return tuple(int(max(0, min(255, value)) * scale + 0.5) for value in rgb)

    def _write_color(self, rgb, multiplier=1.0, force=False):
        if not self.available or self.pixels is None:
            return False

        due, now = self._refresh_due(force)
        if not due:
            return True

        try:
            color = self._scaled(rgb, multiplier)
            if not force and color == self._last_solid_color:
                self.last_refresh_ms = now
                return True
            if hasattr(self.pixels, "fill"):
                self.pixels.fill(color)
            else:
                for index in range(self.count):
                    self.pixels[index] = color
            self.pixels.write()
            self.last_refresh_ms = now
            self._last_solid_color = color
            self._starting_soon_active = ()
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
        level = self._sine_wave(cycle_ms, started=self.pulse_started)

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
            self._last_solid_color = None
            self._starting_soon_active = ()
            return True
        except Exception:
            self.available = False
            self.pixels = None
            return False

    def _sine_wave(self, cycle_ms, started=None):
        if started is None:
            started = self.effect_started
        elapsed = _ticks_diff(_ticks_ms(), started) % cycle_ms
        phase = (elapsed / cycle_ms) * TWO_PI
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
        if not self.available or self.pixels is None:
            return False

        due, now = self._refresh_due(force)
        if not due:
            return True

        now = _ticks_ms()
        led_count = max(1, self.count)
        elapsed = max(0, _ticks_diff(now, self.effect_started))
        head = ((STARTING_SOON_BLOCK_LEDS - 1.0) + (elapsed / STARTING_SOON_LED_MS)) % led_count
        block = min(STARTING_SOON_BLOCK_LEDS, float(led_count))
        tail = min(STARTING_SOON_TAIL_LEDS, float(led_count))

        try:
            background = self._scaled(STARTING_SOON_BACKGROUND)
            levels = self._starting_soon_levels(head, led_count, block, tail)

            if force or not self._starting_soon_active:
                self._fill_pixels(background)
            else:
                for index in self._starting_soon_active:
                    if index not in levels:
                        self.pixels[index] = background

            for index, level in levels.items():
                self.pixels[index] = self._scaled(
                    _mix_rgb(STARTING_SOON_BACKGROUND, STARTING_SOON_CYAN, level)
                )

            self.pixels.write()
            self.last_refresh_ms = now
            self._last_solid_color = None
            self._starting_soon_active = tuple(levels.keys())
            return True
        except Exception:
            self.available = False
            self.pixels = None
            return False

    def _fill_pixels(self, color):
        if hasattr(self.pixels, "fill"):
            self.pixels.fill(color)
            return
        for index in range(self.count):
            self.pixels[index] = color

    def _starting_soon_levels(self, head, led_count, block, tail):
        levels = {}
        head_index = int(head)
        lit_span = int(block + tail)

        for offset in range(lit_span):
            index = (head_index - offset) % led_count
            distance = (head - index) % led_count
            level = 0.0
            if distance < block:
                level = 1.0
            if distance < block + tail:
                fade = 1.0 - ((distance - block + 1.0) / (tail + 1.0))
                level = max(level, _smoothstep(fade))
            if level > levels.get(index, 0.0):
                levels[index] = level

        ahead_index = (head_index + 1) % led_count
        ahead = (ahead_index - head) % led_count
        if ahead < 1.0:
            level = _smoothstep(1.0 - ahead)
            if level > levels.get(ahead_index, 0.0):
                levels[ahead_index] = level

        return levels

    def _render_in_progress(self, force=False):
        level = 0.25 + (0.75 * self._sine_wave(IN_PROGRESS_PULSE_CYCLE_MS))
        return self._write_color((43, 82, 252), level, force=force)

    def _render_ending_soon(self, force=False):
        minutes = float(self.current_command.get("minutes", 5.0))
        threshold = max(1.0, float(self.current_command.get("threshold", 5.0)))
        urgency = 1.0 - min(max(minutes / threshold, 0.0), 1.0)
        cycle_ms = int(1300 - (800 * urgency))
        level = 0.25 + (0.75 * self._sine_wave(max(350, cycle_ms)))
        red = 255
        green = int(150 - (105 * urgency))

        return self._write_color((red, green, 0), level, force=force)
