import time

import network


def connect_wifi_profiles(
    profiles,
    timeout_seconds=20,
    hostname="auto",
    hostname_prefix="zoom-light",
    return_profile=False,
):
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    _disable_power_save(wlan)
    resolved_hostname = _configure_hostname(wlan, hostname, hostname_prefix)

    if wlan.isconnected():
        if return_profile:
            return wlan, None
        return wlan

    last_error = None
    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        ssid = str(profile.get("ssid") or "")
        password = str(profile.get("password") or "")
        if not ssid:
            continue

        try:
            _disconnect(wlan)
            print("Connecting to Wi-Fi:", ssid)
            wlan.connect(ssid, password)
            _wait_connected(wlan, timeout_seconds)
            if resolved_hostname:
                print("Wi-Fi connected:", wlan.ifconfig()[0], resolved_hostname)
            else:
                print("Wi-Fi connected:", wlan.ifconfig()[0])
            if return_profile:
                return wlan, profile
            return wlan
        except Exception as exc:
            last_error = exc
            print("Wi-Fi profile failed:", ssid, exc)

    if last_error is not None:
        raise last_error
    raise RuntimeError("No Wi-Fi profiles configured")


def connect_wifi(ssid, password, timeout_seconds=20, hostname="auto", hostname_prefix="zoom-light"):
    return connect_wifi_profiles(
        [{"ssid": ssid, "password": password}],
        timeout_seconds,
        hostname,
        hostname_prefix,
    )


def _wait_connected(wlan, timeout_seconds):
    started = time.time()
    while not wlan.isconnected():
        if time.time() - started > timeout_seconds:
            raise RuntimeError("Wi-Fi connection timed out")
        time.sleep(0.25)


def _disconnect(wlan):
    try:
        wlan.disconnect()
    except Exception:
        pass


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
