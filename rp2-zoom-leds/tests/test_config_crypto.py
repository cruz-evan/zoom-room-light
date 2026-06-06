from device import config_crypto


def test_encrypt_json_round_trips_payload():
    payload = {
        "schema": 1,
        "version": "test-version",
        "profiles": [{"ssid": "office", "password": "secret"}],
    }
    envelope = config_crypto.encrypt_json(payload, "config-key", b"1234567890abcdef")

    assert envelope["cipher"] == "hmac-sha256-xor"
    assert "office" not in envelope["ciphertext"]
    assert config_crypto.decrypt_json(envelope, "config-key") == payload


def test_decrypt_json_rejects_tampered_ciphertext():
    payload = {"schema": 1, "profiles": [{"ssid": "office", "password": "secret"}]}
    envelope = config_crypto.encrypt_json(payload, "config-key", b"1234567890abcdef")
    envelope["ciphertext"] = "00" + envelope["ciphertext"][2:]

    try:
        config_crypto.decrypt_json(envelope, "config-key")
    except ValueError as exc:
        assert "authentication failed" in str(exc)
    else:
        raise AssertionError("tampered ciphertext must fail authentication")
