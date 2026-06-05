try:
    import secrets as _secrets
except ImportError:
    _secrets = None


def _secret(name, default=""):
    if _secrets is None:
        return default
    return getattr(_secrets, name, default)


def _secret_value(name):
    if _secrets is None or not hasattr(_secrets, name):
        return None
    return getattr(_secrets, name)


def _dict_value(data, name):
    if not isinstance(data, dict):
        return None
    for key in (name, name.lower()):
        if key in data:
            return data[key]
    return None


def _device_hardware_value(name, default):
    hardware = _secret("DEVICE_HARDWARE", {})
    device_hardware = {}
    if isinstance(hardware, dict):
        device_hardware = hardware.get(DEVICE_ID, {}) or {}

    mapped = _dict_value(device_hardware, name)
    if mapped is not None:
        return mapped

    direct = _secret_value(name)
    if direct is not None:
        return direct

    return default


def _device_hardware_int(name, default):
    try:
        return int(_device_hardware_value(name, default))
    except (TypeError, ValueError):
        return int(default)


def _device_hardware_float(name, default):
    try:
        return float(_device_hardware_value(name, default))
    except (TypeError, ValueError):
        return float(default)


def _secret_int(name, default):
    try:
        return int(_secret(name, default))
    except (TypeError, ValueError):
        return int(default)


def _unique_id_hex():
    try:
        import machine

        unique_id = machine.unique_id()
    except Exception:
        return ""

    try:
        return "".join("%02x" % byte for byte in unique_id)
    except Exception:
        return ""


def _resolved_device_id(value):
    value = str(value or "").strip()
    if value and value.lower() not in ("auto", "pico-w", "zoom-led-pico"):
        return value

    unique_id = _unique_id_hex()
    if unique_id:
        return "pico-%s" % unique_id
    return "pico-unknown"


BOARD_UNIQUE_ID = _unique_id_hex()
DEVICE_ID = _resolved_device_id(_secret("DEVICE_ID", "auto"))
ROOM_ID = str(_secret("ROOM_ID", "default-room"))
DEVICE_HOSTNAME = str(_secret("DEVICE_HOSTNAME", "auto"))
DEVICE_HOSTNAME_PREFIX = str(_secret("DEVICE_HOSTNAME_PREFIX", "zoom-light"))

LED_PIN = _device_hardware_int("LED_PIN", 0)
LED_COUNT = _device_hardware_int("LED_COUNT", 144)
BRIGHTNESS = _device_hardware_float("BRIGHTNESS", 0.12)
LED_MAX_REFRESH_FPS = _secret_int("LED_MAX_REFRESH_FPS", 30)
LOOP_DELAY_MS = _secret_int("LOOP_DELAY_MS", 34)
STATUS_BLINK_MS = 500

# Runs once after startup network connectivity is confirmed, then the current
# room state is restored. Disable for a completely quiet boot.
STARTUP_SEQUENCE_ENABLED = True
STARTUP_SEQUENCE_REQUIRES_NETWORK = True
STARTUP_SEQUENCE_STEP_MS = 900
STARTUP_SEQUENCE_COMMANDS = (
    {"mode": "solid", "rgb": [255, 255, 255]},
    {"mode": "pulse", "rgb": [0, 120, 255], "speed": 0.8},
    {"mode": "meeting", "active": True, "participants": 5},
    {"mode": "meeting_status", "state": "starting_soon", "minutes": 5},
    {"mode": "meeting_status", "state": "in_progress"},
    {"mode": "meeting_status", "state": "ending_soon", "minutes": 2},
    {"mode": "off"},
)

# Legacy power-supply bring-up aid. Kept for local USB-only testing when needed.
STARTUP_SELF_TEST = False
STARTUP_SELF_TEST_STEP_MS = STARTUP_SEQUENCE_STEP_MS
STARTUP_SELF_TEST_COMMANDS = STARTUP_SEQUENCE_COMMANDS

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
STATE_POLL_SECONDS = 5
STATE_REQUEST_TIMEOUT_SECONDS = 4
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
TELEMETRY_DEVICE_ID = _resolved_device_id(_secret("TELEMETRY_DEVICE_ID", DEVICE_ID))

RESOURCE_MONITOR_ENABLED = bool(_secret("RESOURCE_MONITOR_ENABLED", TELEMETRY_ENABLED))
RESOURCE_MONITOR_SAMPLE_SECONDS = _secret_int("RESOURCE_MONITOR_SAMPLE_SECONDS", 10)
RESOURCE_MONITOR_CPU_WARN_PERCENT = _secret_int("RESOURCE_MONITOR_CPU_WARN_PERCENT", 80)
RESOURCE_MONITOR_MIN_FREE_BYTES = _secret_int("RESOURCE_MONITOR_MIN_FREE_BYTES", 24000)
RESOURCE_MONITOR_GC_COLLECT = bool(_secret("RESOURCE_MONITOR_GC_COLLECT", False))
RESOURCE_MONITOR_INCLUDE_TEMP = bool(_secret("RESOURCE_MONITOR_INCLUDE_TEMP", True))
