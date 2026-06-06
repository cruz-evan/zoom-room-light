import importlib.util
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_build_module():
    module_path = PROJECT_ROOT / "scripts" / "build_wifi_config.py"
    spec = importlib.util.spec_from_file_location("build_wifi_config", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_payload_uses_existing_wifi_secret_names():
    build_wifi_config = load_build_module()

    payload = build_wifi_config.build_payload(
        {"WIFI_SSID": "office", "WIFI_PASSWORD": "secret"},
        "version-1",
    )

    assert payload == {
        "schema": 1,
        "version": "version-1",
        "profiles": [{"label": "primary", "ssid": "office", "password": "secret"}],
    }


def test_collect_profiles_rejects_partial_pairs():
    build_wifi_config = load_build_module()

    try:
        build_wifi_config.collect_profiles({"WIFI_SSID": "office"})
    except ValueError as exc:
        assert "WIFI_SSID and WIFI_PASSWORD" in str(exc)
    else:
        raise AssertionError("partial Wi-Fi profile must fail")


def test_collect_profiles_supports_fallback_phone_hotspot_secret_names():
    build_wifi_config = load_build_module()

    profiles = build_wifi_config.collect_profiles(
        {
            "OFFICE_WIFI_SSID": "office",
            "OFFICE_WIFI_PASSWORD": "office-secret",
            "FALLBACK_PHONE_HOTSPOT_WIFI_SSID": "phone",
            "FALLBACK_PHONE_HOTSPOT_WIFI_PASSWORD": "phone-secret",
        }
    )

    assert profiles == [
        {"label": "office", "ssid": "office", "password": "office-secret"},
        {"label": "fallback-phone", "ssid": "phone", "password": "phone-secret"},
    ]
