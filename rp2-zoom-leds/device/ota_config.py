try:
    import ujson as json
except ImportError:
    import json

try:
    import urequests as requests
except ImportError:
    import requests

try:
    import usocket as socket
except ImportError:
    import socket

try:
    import uos as os
except ImportError:
    import os

import config_crypto
from wifi_profiles import write_profiles


STATE_FILE = "wifi_config_state.json"


class OtaConfigError(RuntimeError):
    pass


def config_url_from_manifest_url(manifest_url):
    value = str(manifest_url or "")
    if not value:
        return ""
    marker = "/manifest.json"
    if value.endswith(marker):
        return value[: -len(marker)] + "/wifi-config.json"
    if value.endswith("/"):
        return value + "wifi-config.json"
    return value + "/wifi-config.json"


def check_for_config_update(url, key, token="", timeout_seconds=8):
    if not url or not key:
        return "disabled"

    envelope = _fetch_json(url, token, timeout_seconds)
    if not envelope:
        return "missing"

    tag = config_crypto.envelope_tag(envelope)
    if tag and tag == _read_state().get("tag"):
        return "current"

    payload = config_crypto.decrypt_json(envelope, key)
    profiles = _profiles_from_payload(payload)
    if not profiles:
        raise OtaConfigError("encrypted config has no Wi-Fi profiles")

    write_profiles(profiles)
    _write_state({"tag": tag, "version": str(payload.get("version") or "")})
    return "applied"


def _fetch_json(url, token, timeout_seconds=8):
    response = None
    try:
        response = _request_get(url, token, timeout_seconds)
        status = getattr(response, "status_code", 0)
        if status == 404:
            return {}
        if status != 200:
            raise OtaConfigError("config request failed: HTTP %s" % status)
        return response.json()
    finally:
        if response is not None:
            response.close()


def _headers(token):
    if token:
        return {"Authorization": "Bearer %s" % token}
    return {}


def _request_get(url, token, timeout_seconds=8):
    headers = _headers(token)
    try:
        return requests.get(url, headers=headers, timeout=timeout_seconds)
    except TypeError:
        return _request_get_with_socket_timeout(url, headers, timeout_seconds)


def _request_get_with_socket_timeout(url, headers, timeout_seconds):
    previous = None
    can_restore = hasattr(socket, "getdefaulttimeout")
    if can_restore:
        previous = socket.getdefaulttimeout()
    if hasattr(socket, "setdefaulttimeout"):
        socket.setdefaulttimeout(timeout_seconds)
    try:
        return requests.get(url, headers=headers)
    finally:
        if can_restore and hasattr(socket, "setdefaulttimeout"):
            socket.setdefaulttimeout(previous)


def _profiles_from_payload(payload):
    if not isinstance(payload, dict):
        raise OtaConfigError("decrypted config must be an object")
    if int(payload.get("schema", 0)) != 1:
        raise OtaConfigError("unsupported decrypted config schema")
    profiles = payload.get("profiles")
    if not isinstance(profiles, list):
        raise OtaConfigError("decrypted config profiles must be a list")
    return profiles


def _read_state():
    try:
        with open(STATE_FILE, "r") as handle:
            state = json.loads(handle.read())
            if isinstance(state, dict):
                return state
    except Exception:
        pass
    return {}


def _write_state(state):
    temp = STATE_FILE + ".new"
    with open(temp, "w") as handle:
        handle.write(json.dumps(state))
    try:
        os.remove(STATE_FILE)
    except OSError:
        pass
    os.rename(temp, STATE_FILE)
