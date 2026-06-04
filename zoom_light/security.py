from __future__ import annotations

import hashlib
import hmac
import time


def hmac_sha256_hex(secret: str, message: str) -> str:
    return hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def encrypted_validation_token(plain_token: str, secret_token: str) -> str:
    return hmac_sha256_hex(secret_token, plain_token)


def verify_zoom_signature(raw_body: bytes, headers: dict[str, str], secret_token: str) -> bool:
    timestamp = headers.get("x-zm-request-timestamp", "")
    signature = headers.get("x-zm-signature", "")
    if not timestamp or not signature:
        return False

    try:
        request_time = int(timestamp)
    except ValueError:
        return False

    if abs(int(time.time()) - request_time) > 300:
        return False

    message = f"v0:{timestamp}:{raw_body.decode('utf-8')}"
    expected = f"v0={hmac_sha256_hex(secret_token, message)}"
    return hmac.compare_digest(expected, signature)
