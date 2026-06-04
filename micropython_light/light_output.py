try:
    from machine import Pin
except ImportError:
    Pin = None

try:
    import neopixel
except ImportError:
    neopixel = None


COLORS = {
    "red": (255, 0, 0),
    "yellow": (255, 180, 0),
    "green": (0, 255, 60),
    "purple": (120, 0, 255),
}


class LightOutput:
    def __init__(
        self,
        use_neopixel=False,
        led_pin=0,
        led_count=1,
        brightness=0.25,
        use_status_led=False,
        status_led_pin="LED",
        print_changes=True,
    ):
        self.print_changes = print_changes
        self.brightness = brightness
        self._last_key = None
        self._pixel = None
        self._status_led = None

        if use_neopixel:
            if Pin is None or neopixel is None:
                raise RuntimeError("NeoPixel support is not available on this MicroPython build")
            self._pixel = neopixel.NeoPixel(Pin(led_pin), led_count)

        if use_status_led:
            if Pin is None:
                raise RuntimeError("machine.Pin is not available on this MicroPython build")
            self._status_led = Pin(status_led_pin, Pin.OUT)

    def set(self, color, label="", reason=""):
        key = (color, label, reason)
        if key == self._last_key:
            return
        self._last_key = key

        rgb = self._scaled_rgb(COLORS.get(color, COLORS["purple"]))
        if self._pixel is not None:
            for index in range(len(self._pixel)):
                self._pixel[index] = rgb
            self._pixel.write()

        if self._status_led is not None:
            self._status_led.value(0 if color == "green" else 1)

        if self.print_changes:
            print("LIGHT=%s STATUS=%s %s" % (color.upper(), label, reason))

    def _scaled_rgb(self, rgb):
        return tuple(int(channel * self.brightness) for channel in rgb)
