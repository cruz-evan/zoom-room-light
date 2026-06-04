import time

import config
from light_output import LightOutput
from priority import choose_light
from state_client import fetch_state
from wifi_connect import connect_wifi


def main():
    connect_wifi(config.WIFI_SSID, config.WIFI_PASSWORD)

    output = LightOutput(
        use_neopixel=getattr(config, "USE_NEOPIXEL", False),
        led_pin=getattr(config, "LED_PIN", 0),
        led_count=getattr(config, "LED_COUNT", 1),
        brightness=getattr(config, "LED_BRIGHTNESS", 0.25),
        use_status_led=getattr(config, "USE_STATUS_LED", False),
        status_led_pin=getattr(config, "STATUS_LED_PIN", "LED"),
        print_changes=getattr(config, "PRINT_CHANGES", True),
    )

    poll_seconds = getattr(config, "POLL_SECONDS", 2)

    while True:
        try:
            state = fetch_state(config.STATE_URL)
            light = choose_light(state)
            output.set(light["color"], light["label"], light["reason"])
        except Exception as exc:
            output.set("purple", "ERROR", str(exc))
        time.sleep(poll_seconds)


main()
