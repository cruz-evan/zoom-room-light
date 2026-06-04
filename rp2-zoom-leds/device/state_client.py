try:
    import urequests as requests
except ImportError:
    import requests


def fetch_state(url, token=""):
    response = None
    headers = {}
    if token:
        headers["Authorization"] = "Bearer %s" % token

    try:
        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            raise RuntimeError("state request failed: HTTP %s" % response.status_code)
        return response.json()
    finally:
        if response is not None:
            response.close()
