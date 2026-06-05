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


def state_url_for_device(url, device_id=""):
    if not device_id:
        return url
    if "{device_id}" in url:
        return url.replace("{device_id}", str(device_id))
    if "device_id=" in url:
        return url
    separator = "&" if "?" in url else "?"
    return "%s%sdevice_id=%s" % (url, separator, device_id)


def fetch_state(url, token="", device_id="", timeout_seconds=4):
    requests = _requests_module()
    response = None
    headers = {}
    if token:
        headers["Authorization"] = "Bearer %s" % token
    if device_id:
        headers["X-Device-ID"] = str(device_id)

    try:
        _set_default_timeout(timeout_seconds)
        response = _requests_get(
            requests,
            state_url_for_device(url, device_id),
            headers,
            timeout_seconds,
        )
        if response.status_code != 200:
            raise RuntimeError("state request failed: HTTP %s" % response.status_code)
        return response.json()
    finally:
        if response is not None:
            response.close()
