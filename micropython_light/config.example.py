# Copy this file to config.py on the MicroPython device.

WIFI_SSID = ""
WIFI_PASSWORD = ""

# Use your laptop's LAN IP, not localhost.
# Example: "http://192.168.1.42:5050/state"
STATE_URL = "http://YOUR_LAPTOP_IP:5050/state"

POLL_SECONDS = 2

# Print state changes over serial.
PRINT_CHANGES = True

# NeoPixel output.
USE_NEOPIXEL = False
LED_PIN = 0
LED_COUNT = 1
LED_BRIGHTNESS = 0.25

# Plain status LED output. Useful for Pico W onboard LED, but cannot show colors.
USE_STATUS_LED = False
STATUS_LED_PIN = "LED"
