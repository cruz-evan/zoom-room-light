try:
    import ujson as json
except ImportError:
    import json


WIFI_PROFILES_FILE = "wifi_profiles.json"
BOOTSTRAP_PROFILE_SOURCES = (
    ("office", "OFFICE_WIFI_SSID", "OFFICE_WIFI_PASSWORD"),
    ("outpost", "OUTPOST_WIFI_SSID", "OUTPOST_WIFI_PASSWORD"),
    ("primary", "WIFI_SSID", "WIFI_PASSWORD"),
    ("fallback", "WIFI_FALLBACK_SSID", "WIFI_FALLBACK_PASSWORD"),
    (
        "fallback-phone",
        "FALLBACK_PHONE_HOTSPOT_WIFI_SSID",
        "FALLBACK_PHONE_HOTSPOT_WIFI_PASSWORD",
    ),
    ("phone", "PHONE_HOTSPOT_SSID", "PHONE_HOTSPOT_PASSWORD"),
)
PHONE_PROFILE_LABELS = ("fallback-phone", "phone")


def profiles_from_config(config):
    profiles = _load_saved_profiles()

    for profile in _bootstrap_profiles(config):
        profiles.append(profile)

    return _phone_profiles_last(_validated_profiles(profiles))


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


def promote_connected_profile(profiles, connected_profile):
    clean = _validated_profiles(profiles)
    ssid = str((connected_profile or {}).get("ssid") or "")
    if not ssid:
        return clean

    match_index = -1
    for index, profile in enumerate(clean):
        if profile["ssid"] == ssid:
            match_index = index
            break

    if match_index < 0 or _is_phone_profile(clean[match_index]):
        return clean

    ordered = [clean[match_index]]
    ordered.extend(profile for index, profile in enumerate(clean) if index != match_index)
    ordered = _phone_profiles_last(ordered)
    if ordered != clean:
        return write_profiles(ordered)
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


def _phone_profiles_last(profiles):
    regular = []
    phone = []
    for profile in profiles:
        if _is_phone_profile(profile):
            phone.append(profile)
        else:
            regular.append(profile)
    return regular + phone


def _is_phone_profile(profile):
    label = str((profile or {}).get("label") or "")
    return label in PHONE_PROFILE_LABELS
