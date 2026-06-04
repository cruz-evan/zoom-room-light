import time

import network


def connect_wifi(ssid, password, timeout_seconds=20):
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    if wlan.isconnected():
        return wlan

    print("Connecting to Wi-Fi...")
    wlan.connect(ssid, password)

    started = time.time()
    while not wlan.isconnected():
        if time.time() - started > timeout_seconds:
            raise RuntimeError("Wi-Fi connection timed out")
        time.sleep(0.25)

    print("Wi-Fi connected:", wlan.ifconfig()[0])
    return wlan
