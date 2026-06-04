try:
    import uhashlib as hashlib
except ImportError:
    import hashlib

try:
    import ujson as json
except ImportError:
    import json

try:
    import uos as os
except ImportError:
    import os

try:
    import urequests as requests
except ImportError:
    import requests

import gc
import machine
import time


STATE_FILE = "ota_state.json"
EXCLUDED_PATHS = ("secrets.py", "secrets.example.py")


class OtaError(RuntimeError):
    pass


def check_for_update(manifest_url, token="", max_file_bytes=65536):
    manifest = _fetch_json(manifest_url, token)
    version = str(manifest.get("version") or "")
    files = _manifest_files(manifest)

    if not version:
        raise OtaError("manifest is missing version")

    pending = []
    for file_info in files:
        if not _installed_matches(file_info):
            pending.append(file_info)

    if not pending:
        _write_state(version, files)
        return "current"

    print("OTA update available:", version)
    _download_pending(pending, token, max_file_bytes)
    _commit_downloads(pending)
    _write_state(version, files)
    print("OTA update applied; resetting")
    time.sleep_ms(250)
    machine.reset()
    return "applied"


def _fetch_json(url, token):
    response = None
    try:
        response = requests.get(url, headers=_headers(token))
        status = getattr(response, "status_code", 0)
        if status != 200:
            raise OtaError("manifest request failed: HTTP %s" % status)
        return response.json()
    finally:
        if response is not None:
            response.close()


def _fetch_bytes(url, token):
    response = None
    try:
        response = requests.get(url, headers=_headers(token))
        status = getattr(response, "status_code", 0)
        if status != 200:
            raise OtaError("file request failed: HTTP %s" % status)
        return response.content
    finally:
        if response is not None:
            response.close()


def _headers(token):
    if token:
        return {"Authorization": "Bearer %s" % token}
    return {}


def _manifest_files(manifest):
    raw_files = manifest.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise OtaError("manifest has no files")

    files = []
    for raw in raw_files:
        if not isinstance(raw, dict):
            raise OtaError("manifest file entry must be an object")

        path = _validated_path(raw.get("path"))
        sha256 = str(raw.get("sha256") or "").lower()
        url = str(raw.get("url") or "")
        size = _as_int(raw.get("size"), -1)

        if len(sha256) != 64:
            raise OtaError("manifest file has invalid hash")
        if not url:
            raise OtaError("manifest file is missing url")
        if size < 0:
            raise OtaError("manifest file is missing size")

        files.append({"path": path, "sha256": sha256, "url": url, "size": size})

    return files


def _validated_path(path):
    path = str(path or "")
    if (
        not path.endswith(".py")
        or "/" in path
        or "\\" in path
        or path.startswith(".")
        or path in EXCLUDED_PATHS
    ):
        raise OtaError("manifest contains unsupported path")
    return path


def _installed_matches(file_info):
    path = file_info["path"]
    if not _exists(path):
        return False

    try:
        if _file_size(path) != file_info["size"]:
            return False
        return _file_sha256(path) == file_info["sha256"]
    except OSError:
        return False


def _download_pending(files, token, max_file_bytes):
    for file_info in files:
        if file_info["size"] > max_file_bytes:
            raise OtaError("manifest file is too large")

        content = _fetch_bytes(file_info["url"], token)
        try:
            if len(content) != file_info["size"]:
                raise OtaError("downloaded file size mismatch")
            if _bytes_sha256(content) != file_info["sha256"]:
                raise OtaError("downloaded file hash mismatch")

            with open(_temp_name(file_info["path"]), "wb") as handle:
                handle.write(content)
        finally:
            content = None
            gc.collect()


def _commit_downloads(files):
    committed = []
    ordered = list(files)
    ordered.sort(key=lambda file_info: _commit_rank(file_info["path"]))

    try:
        for file_info in ordered:
            path = file_info["path"]
            temp = _temp_name(path)
            backup = _backup_name(path)
            had_existing = _exists(path)

            _remove_if_exists(backup)
            if had_existing:
                os.rename(path, backup)

            try:
                os.rename(temp, path)
            except Exception:
                if had_existing and _exists(backup):
                    os.rename(backup, path)
                raise

            committed.append((path, had_existing))
    except Exception as exc:
        _restore_backups(committed)
        raise OtaError("could not commit update: %s" % exc)

    for path, had_existing in committed:
        if had_existing:
            _remove_if_exists(_backup_name(path))


def _commit_rank(path):
    if path == "ota_client.py":
        return 20
    if path == "boot.py":
        return 30
    if path == "main.py":
        return 40
    return 10


def _restore_backups(committed):
    for path, had_existing in reversed(committed):
        _remove_if_exists(path)
        if had_existing and _exists(_backup_name(path)):
            os.rename(_backup_name(path), path)


def _write_state(version, files):
    state = {
        "version": version,
        "files": [{"path": item["path"], "sha256": item["sha256"]} for item in files],
    }
    with open(STATE_FILE, "w") as handle:
        handle.write(json.dumps(state))


def _file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(512)
            if not chunk:
                break
            digest.update(chunk)
    return _hexlify(digest.digest())


def _bytes_sha256(content):
    digest = hashlib.sha256()
    digest.update(content)
    return _hexlify(digest.digest())


def _hexlify(data):
    return "".join("%02x" % byte for byte in data)


def _file_size(path):
    return os.stat(path)[6]


def _exists(path):
    try:
        os.stat(path)
        return True
    except OSError:
        return False


def _remove_if_exists(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _temp_name(path):
    return path + ".new"


def _backup_name(path):
    return path + ".bak"


def _as_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
