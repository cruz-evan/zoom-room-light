#!/usr/bin/env python3
import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "device"))

import config_crypto  # noqa: E402


PROFILE_SOURCES = (
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


def collect_profiles(env):
    profiles = []
    seen = set()
    for label, ssid_name, password_name in PROFILE_SOURCES:
        ssid = env.get(ssid_name, "")
        password = env.get(password_name, "")
        if not ssid and not password:
            continue
        if not ssid or not password:
            raise ValueError("%s and %s must both be set" % (ssid_name, password_name))
        if ssid in seen:
            continue
        profiles.append({"label": label, "ssid": ssid, "password": password})
        seen.add(ssid)
    return profiles


def build_payload(env, version):
    profiles = collect_profiles(env)
    if not profiles:
        return None
    return {"schema": 1, "version": version, "profiles": profiles}


def parse_args():
    parser = argparse.ArgumentParser(description="Build encrypted Pico Wi-Fi config for GitHub Pages OTA.")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--version", required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    payload = build_payload(os.environ, args.version)
    if payload is None:
        print("No Wi-Fi profiles configured; skipping encrypted wifi-config.json")
        return 0

    key = os.environ.get("OTA_CONFIG_KEY", "")
    if not key:
        print("OTA_CONFIG_KEY is required when publishing encrypted Wi-Fi config", file=sys.stderr)
        return 1

    envelope = config_crypto.encrypt_json(payload, key, os.urandom(16))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(config_crypto.json.dumps(envelope, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("Built encrypted Wi-Fi config with %s profile(s)" % len(payload["profiles"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
