try:
    import uhashlib as hashlib
except ImportError:
    import hashlib

try:
    import ubinascii as binascii
except ImportError:
    import binascii

try:
    import ujson as json
except ImportError:
    import json


SCHEMA = 1
CIPHER = "hmac-sha256-xor"
KDF = "hmac-sha256-v1"
TAG_CONTEXT = b"zoom-room-light:wifi-config:v1"


def encrypt_json(payload, key, nonce):
    plaintext = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ciphertext = _xor_bytes(plaintext, _keystream(_derive_key(key, b"enc"), nonce, len(plaintext)))
    tag = _hmac_sha256(_derive_key(key, b"mac"), TAG_CONTEXT + nonce + ciphertext)
    return {
        "schema": SCHEMA,
        "cipher": CIPHER,
        "kdf": KDF,
        "nonce": _hex(nonce),
        "ciphertext": _hex(ciphertext),
        "tag": _hex(tag),
    }


def decrypt_json(envelope, key):
    if not isinstance(envelope, dict):
        raise ValueError("encrypted config must be an object")
    if int(envelope.get("schema", 0)) != SCHEMA:
        raise ValueError("unsupported encrypted config schema")
    if envelope.get("cipher") != CIPHER:
        raise ValueError("unsupported encrypted config cipher")
    if envelope.get("kdf") != KDF:
        raise ValueError("unsupported encrypted config kdf")

    nonce = _unhex(envelope.get("nonce", ""))
    ciphertext = _unhex(envelope.get("ciphertext", ""))
    expected_tag = _unhex(envelope.get("tag", ""))
    actual_tag = _hmac_sha256(_derive_key(key, b"mac"), TAG_CONTEXT + nonce + ciphertext)
    if not _constant_time_equal(expected_tag, actual_tag):
        raise ValueError("encrypted config authentication failed")

    plaintext = _xor_bytes(ciphertext, _keystream(_derive_key(key, b"enc"), nonce, len(ciphertext)))
    return json.loads(plaintext.decode("utf-8"))


def envelope_tag(envelope):
    if not isinstance(envelope, dict):
        return ""
    return str(envelope.get("tag") or "")


def _derive_key(key, label):
    if isinstance(key, str):
        key = key.encode("utf-8")
    return _hmac_sha256(key, TAG_CONTEXT + b":" + label)


def _keystream(key, nonce, size):
    output = bytearray()
    counter = 0
    while len(output) < size:
        output.extend(_hmac_sha256(key, nonce + _u32be(counter)))
        counter += 1
    return bytes(output[:size])


def _hmac_sha256(key, data):
    if isinstance(key, str):
        key = key.encode("utf-8")
    if isinstance(data, str):
        data = data.encode("utf-8")

    block_size = 64
    if len(key) > block_size:
        key = _sha256(key)
    if len(key) < block_size:
        key = key + (b"\x00" * (block_size - len(key)))

    outer = bytearray(block_size)
    inner = bytearray(block_size)
    for index in range(block_size):
        outer[index] = key[index] ^ 0x5C
        inner[index] = key[index] ^ 0x36
    return _sha256(bytes(outer) + _sha256(bytes(inner) + data))


def _sha256(data):
    digest = hashlib.sha256()
    digest.update(data)
    return digest.digest()


def _xor_bytes(left, right):
    output = bytearray(len(left))
    for index in range(len(left)):
        output[index] = left[index] ^ right[index]
    return bytes(output)


def _u32be(value):
    return bytes(
        (
            (value >> 24) & 0xFF,
            (value >> 16) & 0xFF,
            (value >> 8) & 0xFF,
            value & 0xFF,
        )
    )


def _hex(data):
    value = binascii.hexlify(data)
    if isinstance(value, bytes):
        return value.decode("ascii")
    return value


def _unhex(value):
    if isinstance(value, str):
        value = value.encode("ascii")
    return binascii.unhexlify(value)


def _constant_time_equal(left, right):
    if len(left) != len(right):
        return False
    diff = 0
    for index in range(len(left)):
        diff |= left[index] ^ right[index]
    return diff == 0
