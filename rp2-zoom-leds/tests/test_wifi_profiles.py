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


def test_profiles_from_config_uses_supported_bootstrap_profile_order(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    wifi_profiles = load_wifi_profiles()
    config = types.SimpleNamespace(
        OFFICE_WIFI_SSID="office",
        OFFICE_WIFI_PASSWORD="office-secret",
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
        {"ssid": "primary", "password": "primary-secret", "label": "primary"},
        {"ssid": "fallback", "password": "fallback-secret", "label": "fallback"},
        {
            "ssid": "fallback-phone",
            "password": "fallback-phone-secret",
            "label": "fallback-phone",
        },
        {"ssid": "phone", "password": "phone-secret", "label": "phone"},
    ]
