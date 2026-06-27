import importlib
import json
import types


def load_wifi_profiles():
    return importlib.import_module("device.wifi_profiles")


def test_profiles_from_config_uses_primary_bootstrap_profile_when_no_saved_profiles(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    wifi_profiles = load_wifi_profiles()
    config = types.SimpleNamespace(WIFI_SSID="office", WIFI_PASSWORD="secret")

    assert wifi_profiles.profiles_from_config(config) == [
        {"ssid": "office", "password": "secret", "label": "primary"},
    ]


def test_profiles_from_config_appends_bootstrap_phone_fallback_after_saved_profiles(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    wifi_profiles = load_wifi_profiles()
    (tmp_path / "wifi_profiles.json").write_text(
        json.dumps(
            {
                "profiles": [
                    {"ssid": "office", "password": "old-secret", "label": "office"},
                ],
            }
        ),
        encoding="utf-8",
    )
    config = types.SimpleNamespace(
        WIFI_SSID="office",
        WIFI_PASSWORD="bootstrap-secret",
        PHONE_HOTSPOT_SSID="phone",
        PHONE_HOTSPOT_PASSWORD="phone-secret",
    )

    assert wifi_profiles.profiles_from_config(config) == [
        {"ssid": "office", "password": "old-secret", "label": "office"},
        {"ssid": "phone", "password": "phone-secret", "label": "phone"},
    ]


def test_profiles_from_config_keeps_bootstrap_primary_networks_before_saved_phone(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    wifi_profiles = load_wifi_profiles()
    (tmp_path / "wifi_profiles.json").write_text(
        json.dumps(
            {
                "profiles": [
                    {"ssid": "phone", "password": "phone-secret", "label": "phone"},
                ],
            }
        ),
        encoding="utf-8",
    )
    config = types.SimpleNamespace(
        OUTPOST_WIFI_SSID="outpost",
        OUTPOST_WIFI_PASSWORD="outpost-secret",
        WIFI_SSID="primary",
        WIFI_PASSWORD="primary-secret",
    )

    assert wifi_profiles.profiles_from_config(config) == [
        {"ssid": "outpost", "password": "outpost-secret", "label": "outpost"},
        {"ssid": "primary", "password": "primary-secret", "label": "primary"},
        {"ssid": "phone", "password": "phone-secret", "label": "phone"},
    ]


def test_profiles_from_config_uses_supported_bootstrap_profile_order(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    wifi_profiles = load_wifi_profiles()
    config = types.SimpleNamespace(
        OFFICE_WIFI_SSID="office",
        OFFICE_WIFI_PASSWORD="office-secret",
        OUTPOST_WIFI_SSID="outpost",
        OUTPOST_WIFI_PASSWORD="outpost-secret",
        WIFI_SSID="primary",
        WIFI_PASSWORD="primary-secret",
        WIFI_FALLBACK_SSID="fallback",
        WIFI_FALLBACK_PASSWORD="fallback-secret",
        FALLBACK_PHONE_HOTSPOT_WIFI_SSID="fallback-phone",
        FALLBACK_PHONE_HOTSPOT_WIFI_PASSWORD="fallback-phone-secret",
        PHONE_HOTSPOT_SSID="phone",
        PHONE_HOTSPOT_PASSWORD="phone-secret",
    )

    assert wifi_profiles.profiles_from_config(config) == [
        {"ssid": "office", "password": "office-secret", "label": "office"},
        {"ssid": "outpost", "password": "outpost-secret", "label": "outpost"},
        {"ssid": "primary", "password": "primary-secret", "label": "primary"},
        {"ssid": "fallback", "password": "fallback-secret", "label": "fallback"},
        {
            "ssid": "fallback-phone",
            "password": "fallback-phone-secret",
            "label": "fallback-phone",
        },
        {"ssid": "phone", "password": "phone-secret", "label": "phone"},
    ]


def test_promote_connected_profile_moves_successful_non_phone_profile_to_front(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    wifi_profiles = load_wifi_profiles()
    profiles = [
        {"ssid": "primary", "password": "primary-secret", "label": "primary"},
        {"ssid": "outpost", "password": "outpost-secret", "label": "outpost"},
        {"ssid": "phone", "password": "phone-secret", "label": "phone"},
    ]

    assert wifi_profiles.promote_connected_profile(profiles, profiles[1]) == [
        {"ssid": "outpost", "password": "outpost-secret", "label": "outpost"},
        {"ssid": "primary", "password": "primary-secret", "label": "primary"},
        {"ssid": "phone", "password": "phone-secret", "label": "phone"},
    ]
    assert json.loads((tmp_path / "wifi_profiles.json").read_text(encoding="utf-8")) == {
        "profiles": [
            {"ssid": "outpost", "password": "outpost-secret", "label": "outpost"},
            {"ssid": "primary", "password": "primary-secret", "label": "primary"},
            {"ssid": "phone", "password": "phone-secret", "label": "phone"},
        ]
    }


def test_promote_connected_profile_does_not_move_phone_before_primary_networks(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    wifi_profiles = load_wifi_profiles()
    profiles = [
        {"ssid": "primary", "password": "primary-secret", "label": "primary"},
        {"ssid": "outpost", "password": "outpost-secret", "label": "outpost"},
        {"ssid": "phone", "password": "phone-secret", "label": "phone"},
    ]

    assert wifi_profiles.promote_connected_profile(profiles, profiles[2]) == profiles
    assert not (tmp_path / "wifi_profiles.json").exists()
