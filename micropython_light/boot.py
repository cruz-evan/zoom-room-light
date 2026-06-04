import time

import network

import config


WEBREPL_PORT = 8266


def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    if not wlan.isconnected():
        print("Connecting to Wi-Fi...")
        wlan.connect(config.WIFI_SSID, config.WIFI_PASSWORD)

    timeout_seconds = getattr(config, "WIFI_TIMEOUT_SECONDS", 20)
    started = time.time()
    while not wlan.isconnected():
        if time.time() - started > timeout_seconds:
            raise RuntimeError("Wi-Fi connection timed out")
        time.sleep(0.25)

    print("Wi-Fi connected:", wlan.ifconfig()[0])
    return wlan


def start_webrepl(wlan):
    if not getattr(config, "WEBREPL_ENABLED", False):
        return

    password = getattr(config, "WEBREPL_PASSWORD", "")
    if not password:
        raise RuntimeError("WEBREPL_PASSWORD must be set when WEBREPL_ENABLED is True")

    import webrepl

    webrepl.start(password=password)
    print("WebREPL ready at ws://%s:%s" % (wlan.ifconfig()[0], WEBREPL_PORT))


wlan = connect_wifi()
start_webrepl(wlan)
