try:
    import ujson as json
except ImportError:
    import json

try:
    import urequests as requests
except ImportError:
    import requests


def fetch_state(url):
    response = None
    try:
        response = requests.get(url)
        if response.status_code != 200:
            raise RuntimeError("state request failed: HTTP %s" % response.status_code)
        return response.json()
    finally:
        if response is not None:
            response.close()
