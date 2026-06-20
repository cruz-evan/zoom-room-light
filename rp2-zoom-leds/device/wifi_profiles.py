try:
    import ujson as json
except ImportError:
    import json


WIFI_PROFILES_FILE = "wifi_profiles.json"
BOOTSTRAP_PROFILE_SOURCES = (
    ("office", "OFFICE_WIFI_SSID", "OFFICE_WIFI_PASSWORD"),
    ("primary", "WIFI_SSID", "WIFI_PASSWORD"),
    ("fallback", "WIFI_FALLBACK_SSID", "WIFI_FALLBACK_PASSWORD"),
    (
        "fallback-phone",
        "FALLBACK_PHONE_HOTSPOT_WIFI_SSID",
        "FALLBACK_PHONE_HOTSPOT_WIFI_PASSWORD",
    ),
    ("phone", "PHONE_HOTSPOT_SSID", "PHONE_HOTSPOT_PASSWORD"),
)


def profiles_from_config(config):
    profiles = _load_saved_profiles()
    seen = set(profile["ssid"] for profile in profiles if isinstance(profile, dict))

    for profile in _bootstrap_profiles(config):
        if profile["ssid"] in seen:
            continue
        profiles.append(profile)
        seen.add(profile["ssid"])

    return profiles


def _bootstrap_profiles(config):
    profiles = []
    for label, ssid_name, password_name in BOOTSTRAP_PROFILE_SOURCES:
        ssid = str(getattr(config, ssid_name, "") or "")
        password = str(getattr(config, password_name, "") or "")
        if ssid:
            profiles.append({"ssid": ssid, "password": password, "label": label})
    return profiles


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
