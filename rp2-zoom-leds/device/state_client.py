try:
    import gc
except ImportError:
    gc = None


def _requests_module():
    try:
        import urequests as requests
    except ImportError:
        import requests
    return requests


def _socket_module():
    try:
        import usocket as socket
    except ImportError:
        import socket
    return socket


def _cache_bust_token():
    try:
        import time
        if hasattr(time, "ticks_ms"):
            return str(time.ticks_ms())
    except Exception:
        pass
    return None


def _set_default_timeout(seconds):
    try:
        socket = _socket_module()
        socket.setdefaulttimeout(seconds)
    except Exception:
        pass


def _requests_get(requests, url, headers, timeout_seconds):
    try:
        return requests.get(url, headers=headers, timeout=timeout_seconds)
    except TypeError:
        return requests.get(url, headers=headers)


def _append_query_param(url, key, value):
    if value is None or value == "":
        return url
    separator = "&" if "?" in url else "?"
    return "%s%s%s=%s" % (url, separator, key, value)


def state_url_for_device(url, device_id="", cache_bust=None):
    resolved = url
    if not device_id:
        return _append_query_param(resolved, "_", cache_bust)
    if "{device_id}" in resolved:
        resolved = resolved.replace("{device_id}", str(device_id))
        return _append_query_param(resolved, "_", cache_bust)
    if "device_id=" not in resolved:
        resolved = _append_query_param(resolved, "device_id", device_id)
    return _append_query_param(resolved, "_", cache_bust)


def fetch_state(url, token="", device_id="", timeout_seconds=4):
    requests = _requests_module()
    response = None
    headers = {}
    if token:
        headers["Authorization"] = "Bearer %s" % token
    if device_id:
        headers["X-Device-ID"] = str(device_id)
    headers["Cache-Control"] = "no-cache"
    headers["Pragma"] = "no-cache"

    try:
        if gc is not None:
            gc.collect()
        _set_default_timeout(timeout_seconds)
        response = _requests_get(
            requests,
            state_url_for_device(url, device_id, _cache_bust_token()),
            headers,
            timeout_seconds,
        )
        if response.status_code != 200:
            raise RuntimeError("state request failed: HTTP %s" % response.status_code)
        return response.json()
    finally:
        if response is not None:
            response.close()
        if gc is not None:
            gc.collect()
