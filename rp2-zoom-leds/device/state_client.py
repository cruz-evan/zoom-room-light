def _requests_module():
    try:
        import urequests as requests
    except ImportError:
        import requests
    return requests


def state_url_for_device(url, device_id=""):
    if not device_id:
        return url
    if "{device_id}" in url:
        return url.replace("{device_id}", str(device_id))
    if "device_id=" in url:
        return url
    separator = "&" if "?" in url else "?"
    return "%s%sdevice_id=%s" % (url, separator, device_id)


def fetch_state(url, token="", device_id=""):
    requests = _requests_module()
    response = None
    headers = {}
    if token:
        headers["Authorization"] = "Bearer %s" % token
    if device_id:
        headers["X-Device-ID"] = str(device_id)

    try:
        response = requests.get(state_url_for_device(url, device_id), headers=headers)
        if response.status_code != 200:
            raise RuntimeError("state request failed: HTTP %s" % response.status_code)
        return response.json()
    finally:
        if response is not None:
            response.close()
