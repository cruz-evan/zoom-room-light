LED_PIN = 0
LED_COUNT = 144
BRIGHTNESS = 0.12
LOOP_DELAY_MS = 20
STATUS_BLINK_MS = 500

# Power-supply bring-up aid. Disable for normal installed behavior once the
# cloud/serial bridge is driving real meeting state.
STARTUP_SELF_TEST = True
STARTUP_SELF_TEST_STEP_MS = 2500
STARTUP_SELF_TEST_COMMANDS = (
    {"mode": "meeting_status", "state": "starting_soon", "minutes": 5},
    {"mode": "meeting_status", "state": "in_progress"},
    {"mode": "meeting_status", "state": "ending_soon", "minutes": 2},
    {"mode": "meeting_status", "state": "in_progress"},
)

try:
    import secrets as _secrets
except ImportError:
    _secrets = None


def _secret(name, default=""):
    if _secrets is None:
        return default
    return getattr(_secrets, name, default)


WIFI_SSID = _secret("WIFI_SSID")
WIFI_PASSWORD = _secret("WIFI_PASSWORD")
STATE_URL = _secret("STATE_URL")
DEVICE_TOKEN = _secret("DEVICE_TOKEN")
OTA_MANIFEST_URL = _secret("OTA_MANIFEST_URL")
OTA_TOKEN = _secret("OTA_TOKEN")

NETWORK_ENABLED = bool(WIFI_SSID and (STATE_URL or OTA_MANIFEST_URL))
NETWORK_THREAD_ENABLED = True
NETWORK_THREAD_IDLE_MS = 20
WIFI_CONNECT_TIMEOUT_SECONDS = 20
STATE_POLL_SECONDS = 60
STATE_ERROR_RETRY_SECONDS = 10
SERIAL_OVERRIDE_SECONDS = 10
NETWORK_ERROR_AFTER_FAILURES = 3
NETWORK_ERROR_COMMAND = {"mode": "pulse", "rgb": [120, 0, 255], "speed": 0.45}
OTA_ENABLED = bool(WIFI_SSID and OTA_MANIFEST_URL)
OTA_INITIAL_DELAY_SECONDS = 20
OTA_CHECK_SECONDS = 60
OTA_ERROR_RETRY_SECONDS = 60
OTA_MAX_FILE_BYTES = 65536

TELEMETRY_ENABLED = bool(_secret("TELEMETRY_ENABLED", False))
TELEMETRY_HOST = _secret("TELEMETRY_HOST", "255.255.255.255")
TELEMETRY_PORT = int(_secret("TELEMETRY_PORT", 9977) or 9977)
TELEMETRY_DEVICE_ID = _secret("TELEMETRY_DEVICE_ID", "pico-w")
