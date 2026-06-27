import time

from machine import Pin
import neopixel


PULSE_TABLE_32 = (
    0, 2, 10, 21, 37, 57, 79, 103,
    127, 152, 176, 198, 218, 234, 245, 253,
    255, 253, 245, 234, 218, 198, 176, 152,
    128, 103, 79, 57, 37, 21, 10, 2,
)
PULSE_TABLE_STEPS = 32
PULSE_DIM_SCALE_255 = 64
GENERIC_PULSE_MIN_SCALE_255 = 20
IN_PROGRESS_PULSE_CYCLE_MS = 5166
STARTING_SOON_CYAN = (44, 213, 252)
STARTING_SOON_IN_PROGRESS_BLUE = (43, 82, 252)
STARTING_SOON_PULSE_CYCLE_MS = 1800
STARTING_SOON_SCRAMBLE_FRAMES = 4


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
        self.brightness_255 = int(self.brightness * 255 + 0.5)
        self.frame_interval_ms = _frame_interval_ms(max_refresh_fps)
        self.last_refresh_ms = None
        self.pixels = None
        self.available = False
        self.current_command = {"mode": "off"}
        self.pulse_started = _ticks_ms()
        self.effect_started = _ticks_ms()
        self._last_solid_color = None
        self._starting_soon_frame = 0
        self._starting_soon_mask = None
        self._starting_soon_mask_frame = -1
        self._starting_soon_mask_threshold = -1
        self._starting_soon_last_key = None

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
        return self._scaled_int(rgb, int(255 * multiplier + 0.5))

    def _scaled_int(self, rgb, scale_255=255):
        scale_255 = max(0, min(255, int(scale_255)))
        total_scale = self.brightness_255 * scale_255
        return tuple(
            (max(0, min(255, int(value))) * total_scale + 32512) // 65025
            for value in rgb
        )

    def _write_color(self, rgb, multiplier=1.0, force=False):
        return self._write_color_scale(rgb, int(255 * multiplier + 0.5), force=force)

    def _write_color_scale(self, rgb, scale_255=255, force=False):
        if not self.available or self.pixels is None:
            return False

        due, now = self._refresh_due(force)
        if not due:
            return True

        try:
            color = self._scaled_int(rgb, scale_255)
            if not force and color == self._last_solid_color:
                self.last_refresh_ms = now
                return True
            self._fill_pixels(color)
            self.pixels.write()
            self.last_refresh_ms = now
            self._last_solid_color = color
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
        scale = self._pulse_scale(cycle_ms, GENERIC_PULSE_MIN_SCALE_255, 255, started=self.pulse_started)

        return self._write_color_scale(rgb, scale, force=force)

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
            return True
        except Exception:
            self.available = False
            self.pixels = None
            return False

    def _pulse_index(self, cycle_ms, started=None):
        if started is None:
            started = self.effect_started
        elapsed = _ticks_diff(_ticks_ms(), started) % cycle_ms
        return (elapsed * PULSE_TABLE_STEPS) // cycle_ms

    def _pulse_scale(self, cycle_ms, min_scale_255, max_scale_255=255, started=None):
        pulse = PULSE_TABLE_32[self._pulse_index(cycle_ms, started=started)]
        return min_scale_255 + (((max_scale_255 - min_scale_255) * pulse + 127) // 255)

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

        try:
            blend = self._starting_soon_blend_amount()
            scale = self._pulse_scale(
                STARTING_SOON_PULSE_CYCLE_MS,
                PULSE_DIM_SCALE_255,
                255,
                started=self.effect_started,
            )
            starting_color = self._scaled_int(STARTING_SOON_CYAN, scale)
            in_progress_color = self._scaled_int(STARTING_SOON_IN_PROGRESS_BLUE, scale)

            if blend <= 0.0:
                key = (0, scale)
                if not force and key == self._starting_soon_last_key:
                    self.last_refresh_ms = now
                    return True
                self._fill_pixels(starting_color)
            elif blend >= 1.0:
                key = (1, scale)
                if not force and key == self._starting_soon_last_key:
                    self.last_refresh_ms = now
                    return True
                self._fill_pixels(in_progress_color)
            else:
                self._starting_soon_frame += 1
                threshold = int(256 * blend)
                frame = self._starting_soon_frame // STARTING_SOON_SCRAMBLE_FRAMES
                key = (2, scale, frame, threshold)
                if not force and key == self._starting_soon_last_key:
                    self.last_refresh_ms = now
                    return True
                self._write_starting_soon_mix(starting_color, in_progress_color, frame, threshold)

            self.pixels.write()
            self.last_refresh_ms = now
            self._last_solid_color = None
            self._starting_soon_last_key = key
            return True
        except Exception:
            self.available = False
            self.pixels = None
            return False

    def _starting_soon_blend_amount(self):
        seconds = self.current_command.get("seconds_until_expected_state_change")
        if seconds is None:
            return 0.0

        try:
            remaining = float(seconds)
        except (TypeError, ValueError):
            return 0.0

        try:
            threshold_minutes = float(
                self.current_command.get("threshold", self.current_command.get("minutes", 15.0))
            )
        except (TypeError, ValueError):
            threshold_minutes = 15.0

        total = max(1.0, threshold_minutes * 60.0)
        return _clamp_unit(1.0 - (max(0.0, remaining) / total))

    def _starting_soon_blend_pixel(self, index, frame, threshold):
        if threshold <= 0:
            return False
        if threshold >= 256:
            return True
        value = (int(index) * 1103515245 + int(frame) * 12345 + 0x45D9F3B) & 0xFFFFFFFF
        value = (value ^ (value >> 16)) & 0xFF
        return value < threshold

    def _write_starting_soon_mix(self, starting_color, in_progress_color, frame, threshold):
        mask = self._starting_soon_pixel_mask(frame, threshold)
        buf = getattr(self.pixels, "buf", None)
        if buf is not None and self.count > 0:
            bpp = len(buf) // self.count
            starting_encoded = bytes(self._encoded_pixel(starting_color, bpp))
            in_progress_encoded = bytes(self._encoded_pixel(in_progress_color, bpp))
            for index in range(self.count):
                offset = index * bpp
                if mask[index]:
                    buf[offset : offset + bpp] = in_progress_encoded
                else:
                    buf[offset : offset + bpp] = starting_encoded
            return

        for index in range(self.count):
            if mask[index]:
                self.pixels[index] = in_progress_color
            else:
                self.pixels[index] = starting_color

    def _starting_soon_pixel_mask(self, frame, threshold):
        mask = self._starting_soon_mask
        if mask is None or len(mask) != self.count:
            mask = bytearray(self.count)
            self._starting_soon_mask = mask
            self._starting_soon_mask_frame = -1
            self._starting_soon_mask_threshold = -1

        if frame != self._starting_soon_mask_frame or threshold != self._starting_soon_mask_threshold:
            for index in range(self.count):
                mask[index] = 1 if self._starting_soon_blend_pixel(index, frame, threshold) else 0
            self._starting_soon_mask_frame = frame
            self._starting_soon_mask_threshold = threshold

        return mask

    def _fill_pixels(self, color):
        buf = getattr(self.pixels, "buf", None)
        if buf is not None and self.count > 0:
            bpp = len(buf) // self.count
            encoded = self._encoded_pixel(color, bpp)
            buf[:] = bytes(encoded) * self.count
            return

        if hasattr(self.pixels, "fill"):
            self.pixels.fill(color)
            return
        for index in range(self.count):
            self.pixels[index] = color

    def _encoded_pixel(self, color, bpp):
        order = getattr(self.pixels, "ORDER", (1, 0, 2, 3))
        encoded = bytearray(bpp)
        for index in range(min(bpp, len(color))):
            target = order[index] if index < len(order) else index
            if target < bpp:
                encoded[target] = color[index]
        return encoded

    def _render_in_progress(self, force=False):
        scale = self._pulse_scale(IN_PROGRESS_PULSE_CYCLE_MS, PULSE_DIM_SCALE_255, 255)
        return self._write_color_scale((43, 82, 252), scale, force=force)

    def _render_ending_soon(self, force=False):
        minutes = float(self.current_command.get("minutes", 5.0))
        threshold = max(1.0, float(self.current_command.get("threshold", 5.0)))
        urgency = 1.0 - min(max(minutes / threshold, 0.0), 1.0)
        cycle_ms = int(1300 - (800 * urgency))
        scale = self._pulse_scale(max(350, cycle_ms), PULSE_DIM_SCALE_255, 255)
        red = 255
        green = int(150 - (105 * urgency))

        return self._write_color_scale((red, green, 0), scale, force=force)
