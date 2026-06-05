import time

import network


def connect_wifi(ssid, password, timeout_seconds=20, hostname="auto", hostname_prefix="zoom-light"):
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    _disable_power_save(wlan)
    resolved_hostname = _configure_hostname(wlan, hostname, hostname_prefix)

    if wlan.isconnected():
        return wlan

    print("Connecting to Wi-Fi...")
    wlan.connect(ssid, password)

    started = time.time()
    while not wlan.isconnected():
        if time.time() - started > timeout_seconds:
            raise RuntimeError("Wi-Fi connection timed out")
        time.sleep(0.25)

    if resolved_hostname:
        print("Wi-Fi connected:", wlan.ifconfig()[0], resolved_hostname)
    else:
        print("Wi-Fi connected:", wlan.ifconfig()[0])
    return wlan


def _disable_power_save(wlan):
    try:
        wlan.config(pm=network.WLAN.PM_NONE)
    except Exception:
        pass


def _configure_hostname(wlan, hostname="auto", hostname_prefix="zoom-light"):
    resolved = _resolve_hostname(wlan, hostname, hostname_prefix)
    if not resolved:
        return ""

    try:
        network.hostname(resolved)
        return resolved
    except Exception:
        pass

    try:
        wlan.config(hostname=resolved)
        return resolved
    except Exception:
        return ""


def _resolve_hostname(wlan, hostname="auto", hostname_prefix="zoom-light"):
    value = str(hostname or "").strip()
    if value and value.lower() not in ("auto", "mac", "mac-address"):
        suffix = _mac_suffix(wlan)
        if "{mac}" in value and suffix:
            return value.replace("{mac}", suffix)
        return value

    suffix = _mac_suffix(wlan)
    prefix = str(hostname_prefix or "zoom-light").strip() or "zoom-light"
    if suffix:
        return "%s-%s" % (prefix, suffix)
    return prefix


def _mac_suffix(wlan):
    try:
        mac = wlan.config("mac")
    except Exception:
        return ""

    try:
        return "".join("%02x" % byte for byte in mac[-3:])
    except Exception:
        return ""
