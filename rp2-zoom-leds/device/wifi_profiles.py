try:
    import ujson as json
except ImportError:
    import json


WIFI_PROFILES_FILE = "wifi_profiles.json"


def profiles_from_config(config):
    profiles = _load_saved_profiles()
    if profiles:
        return profiles

    ssid = str(getattr(config, "WIFI_SSID", "") or "")
    password = str(getattr(config, "WIFI_PASSWORD", "") or "")
    if ssid:
        return [{"ssid": ssid, "password": password, "source": "secrets"}]
    return []


def write_profiles(profiles):
    clean = _validated_profiles(profiles)
    with open(WIFI_PROFILES_FILE, "w") as handle:
        handle.write(json.dumps({"profiles": clean}))
    return clean


def _load_saved_profiles():
    try:
        with open(WIFI_PROFILES_FILE, "r") as handle:
            payload = json.loads(handle.read())
    except Exception:
        return []
    return _validated_profiles(payload.get("profiles") if isinstance(payload, dict) else [])


def _validated_profiles(raw_profiles):
    profiles = []
    seen = set()
    if not isinstance(raw_profiles, list):
        return profiles

    for raw in raw_profiles:
        if not isinstance(raw, dict):
            continue
        ssid = str(raw.get("ssid") or "")
        password = str(raw.get("password") or "")
        label = str(raw.get("label") or raw.get("source") or "")
        if not ssid or ssid in seen:
            continue
        item = {"ssid": ssid, "password": password}
        if label:
            item["label"] = label
        profiles.append(item)
        seen.add(ssid)
    return profiles
