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

try:
    import usocket as socket
except ImportError:
    import socket

import gc
import machine
import time

try:
    import build_info
except ImportError:
    build_info = None


STATE_FILE = "ota_state.json"
BAD_VERSIONS_FILE = "ota_bad.json"
PRETRIAL_FAILURES_FILE = "ota_failures.json"
EXCLUDED_PATHS = ("secrets.py", "secrets.example.py", "boot.py", "ota_client.py")
TRIAL_COMMIT = "trial_commit"
TRIAL_PENDING = "trial_pending"
TRIAL_RUNNING = "trial_running"
CONFIRMED = "confirmed"
MAX_TRIAL_BOOTS = 1
MAX_BAD_VERSIONS = 8
MAX_PRETRIAL_FAILURES = 3
MAX_PRETRIAL_FAILURE_RECORDS = 8
MIN_FREE_AFTER_OTA_BYTES = 64 * 1024


class OtaError(RuntimeError):
    pass


def check_for_update(
    manifest_url,
    token="",
    max_file_bytes=65536,
    timeout_seconds=8,
    telemetry=None,
):
    manifest_started = _ticks_ms()
    _log_telemetry(telemetry, "ota_pretrial_manifest_fetch_start")
    manifest = _fetch_json(manifest_url, token, timeout_seconds)
    version = str(manifest.get("version") or "")
    manifest_build_epoch = _manifest_build_epoch(manifest)
    files = _manifest_files(manifest)
    _log_telemetry(
        telemetry,
        "ota_pretrial_manifest_fetch_done",
        version=version,
        build_epoch_utc=manifest_build_epoch,
        file_count=len(files),
        elapsed_ms=_elapsed_ms(manifest_started),
    )

    if not version:
        raise OtaError("manifest is missing version")
    _remove_download_temps(files)
    if _is_bad_version(version):
        print("OTA version is marked bad; skipping:", version)
        _log_telemetry(
            telemetry,
            "ota_pretrial_decision",
            version=version,
            build_epoch_utc=manifest_build_epoch,
            status="bad",
            pending_count=0,
        )
        return "bad"

    pending = []
    for file_info in files:
        if not _installed_matches(file_info):
            pending.append(file_info)

    if not pending:
        _clear_pretrial_failure(version)
        _write_confirmed_state(version, manifest_build_epoch, files)
        _log_telemetry(
            telemetry,
            "ota_pretrial_decision",
            version=version,
            build_epoch_utc=manifest_build_epoch,
            status="current",
            pending_count=0,
            total_file_count=len(files),
        )
        return "current"

    installed_build_epoch = _installed_build_epoch()
    if manifest_build_epoch <= installed_build_epoch:
        print(
            "OTA manifest is not newer; skipping:",
            manifest_build_epoch,
            "<=",
            installed_build_epoch,
        )
        _log_telemetry(
            telemetry,
            "ota_pretrial_decision",
            version=version,
            build_epoch_utc=manifest_build_epoch,
            installed_build_epoch=installed_build_epoch,
            status="stale",
            pending_count=len(pending),
            total_file_count=len(files),
        )
        return "stale"

    print("OTA update available:", version)
    _log_telemetry(
        telemetry,
        "ota_pretrial_decision",
        version=version,
        build_epoch_utc=manifest_build_epoch,
        installed_build_epoch=installed_build_epoch,
        status="update_available",
        pending_count=len(pending),
        total_file_count=len(files),
        pending_bytes=_sum_file_sizes(pending),
    )
    previous_state = None
    trial_state = None
    phase = "space_check"
    try:
        space = _ensure_free_space(pending)
        _log_telemetry(
            telemetry,
            "ota_pretrial_space_check_done",
            version=version,
            build_epoch_utc=manifest_build_epoch,
            **space
        )
        phase = "download"
        _download_pending(pending, token, max_file_bytes, timeout_seconds, telemetry)
        previous_state = _read_json(STATE_FILE, {})
        trial_state = _trial_state(version, manifest_build_epoch, files, pending, previous_state)
        phase = "state_write_commit"
        _log_telemetry(
            telemetry,
            "ota_pretrial_state_write_start",
            version=version,
            build_epoch_utc=manifest_build_epoch,
            status=TRIAL_COMMIT,
        )
        _write_json(STATE_FILE, trial_state)
        _log_telemetry(
            telemetry,
            "ota_pretrial_state_write_done",
            version=version,
            build_epoch_utc=manifest_build_epoch,
            status=TRIAL_COMMIT,
        )
        phase = "commit"
        _log_telemetry(
            telemetry,
            "ota_pretrial_commit_start",
            version=version,
            build_epoch_utc=manifest_build_epoch,
            pending_count=len(pending),
        )
        _commit_downloads(pending, keep_backups=True)
        _log_telemetry(
            telemetry,
            "ota_pretrial_commit_done",
            version=version,
            build_epoch_utc=manifest_build_epoch,
            pending_count=len(pending),
        )
    except Exception as exc:
        _log_telemetry(
            telemetry,
            "ota_pretrial_error",
            version=version,
            build_epoch_utc=manifest_build_epoch,
            phase=phase,
            error=_short_error(exc),
        )
        _remove_download_temps(pending)
        if trial_state is not None:
            _restore_trial_backups(trial_state)
            if previous_state:
                _write_json(STATE_FILE, previous_state)
            else:
                _remove_if_exists(STATE_FILE)
        try:
            failure = _record_pretrial_failure(version, manifest_build_epoch, str(exc))
            _log_telemetry(
                telemetry,
                "ota_pretrial_failure_recorded",
                version=version,
                build_epoch_utc=manifest_build_epoch,
                count=failure.get("count", 0),
                marked_bad=bool(failure.get("marked_bad", False)),
            )
        except Exception:
            pass
        raise
    _clear_pretrial_failure(version)
    trial_state["status"] = TRIAL_PENDING
    trial_state["trial_boots"] = 0
    _log_telemetry(
        telemetry,
        "ota_pretrial_state_write_start",
        version=version,
        build_epoch_utc=manifest_build_epoch,
        status=TRIAL_PENDING,
    )
    _write_json(STATE_FILE, trial_state)
    _log_telemetry(
        telemetry,
        "ota_pretrial_staged",
        version=version,
        build_epoch_utc=manifest_build_epoch,
        status=TRIAL_PENDING,
        pending_count=len(pending),
    )
    print("OTA update staged for trial; resetting")
    time.sleep_ms(250)
    machine.reset()
    return "trial"


def prepare_trial_boot():
    state = _read_json(STATE_FILE, {})
    status = state.get("status")
    if status == TRIAL_RUNNING:
        return rollback_trial_update("trial_interrupted", reset=True)
    if status == TRIAL_COMMIT:
        return rollback_trial_update("commit_interrupted", reset=True)
    if status != TRIAL_PENDING:
        return "none"

    trial_boots = _as_int(state.get("trial_boots"), 0) + 1
    if trial_boots > MAX_TRIAL_BOOTS:
        return rollback_trial_update("trial_boot_limit", reset=True)

    state["trial_boots"] = trial_boots
    state["status"] = TRIAL_RUNNING
    _write_json(STATE_FILE, state)
    return "running"


def trial_update_running():
    state = _read_json(STATE_FILE, {})
    return state.get("status") == TRIAL_RUNNING


def confirm_trial_boot():
    state = _read_json(STATE_FILE, {})
    if state.get("status") not in (TRIAL_PENDING, TRIAL_RUNNING):
        return "none"

    files = state.get("files") or ()
    for file_info in files:
        if not _installed_matches(file_info):
            return rollback_trial_update("candidate_hash_mismatch", reset=True)

    _remove_trial_backups(state)
    _remove_trial_temps(state)
    _write_confirmed_state(
        str(state.get("version") or ""),
        _as_int(state.get("build_epoch_utc"), 0),
        files,
    )
    return "confirmed"


def rollback_trial_update(reason="trial_failed", reset=False):
    state = _read_json(STATE_FILE, {})
    if state.get("status") not in (TRIAL_COMMIT, TRIAL_PENDING, TRIAL_RUNNING):
        return "none"

    _restore_trial_backups(state)
    _remove_trial_temps(state)
    try:
        _mark_bad_version(state, reason)
    except Exception:
        pass
    previous_state = state.get("previous_state")
    if isinstance(previous_state, dict) and previous_state:
        previous_state["status"] = CONFIRMED
        _write_json(STATE_FILE, previous_state)
    else:
        _remove_if_exists(STATE_FILE)

    if reset:
        print("OTA trial rolled back; resetting:", reason)
        time.sleep_ms(250)
        machine.reset()
    return "rolled_back"


def _fetch_json(url, token, timeout_seconds=8):
    response = None
    try:
        response = _request_get(url, token, timeout_seconds)
        status = getattr(response, "status_code", 0)
        if status != 200:
            raise OtaError("manifest request failed: HTTP %s" % status)
        return response.json()
    finally:
        if response is not None:
            response.close()


def _fetch_bytes(url, token, timeout_seconds=8):
    response = None
    try:
        response = _request_get(url, token, timeout_seconds)
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


def _request_get(url, token, timeout_seconds=8):
    headers = _headers(token)
    try:
        return requests.get(url, headers=headers, timeout=timeout_seconds)
    except TypeError:
        return _request_get_with_socket_timeout(url, headers, timeout_seconds)


def _request_get_with_socket_timeout(url, headers, timeout_seconds):
    previous = None
    can_restore = hasattr(socket, "getdefaulttimeout")
    if can_restore:
        previous = socket.getdefaulttimeout()
    if hasattr(socket, "setdefaulttimeout"):
        socket.setdefaulttimeout(timeout_seconds)
    try:
        return requests.get(url, headers=headers)
    finally:
        if can_restore and hasattr(socket, "setdefaulttimeout"):
            socket.setdefaulttimeout(previous)


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


def _manifest_build_epoch(manifest):
    epoch = _as_int(manifest.get("build_epoch_utc"), 0)
    if epoch < 0:
        raise OtaError("manifest has invalid build timestamp")
    return epoch


def _installed_build_epoch():
    if build_info is None:
        return 0
    epoch = _as_int(getattr(build_info, "BUILD_EPOCH_UTC", 0), 0)
    if epoch < 0:
        return 0
    return epoch


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


def _download_pending(files, token, max_file_bytes, timeout_seconds=8, telemetry=None):
    total = len(files)
    for index, file_info in enumerate(files, 1):
        if file_info["size"] > max_file_bytes:
            raise OtaError("manifest file is too large")

        started = _ticks_ms()
        _log_telemetry(
            telemetry,
            "ota_pretrial_file_download_start",
            path=file_info["path"],
            index=index,
            total=total,
            size=file_info["size"],
        )
        content = _fetch_bytes(file_info["url"], token, timeout_seconds)
        try:
            if len(content) != file_info["size"]:
                raise OtaError("downloaded file size mismatch")
            if _bytes_sha256(content) != file_info["sha256"]:
                raise OtaError("downloaded file hash mismatch")

            with open(_temp_name(file_info["path"]), "wb") as handle:
                handle.write(content)
            _log_telemetry(
                telemetry,
                "ota_pretrial_file_download_done",
                path=file_info["path"],
                index=index,
                total=total,
                size=file_info["size"],
                elapsed_ms=_ticks_diff(_ticks_ms(), started),
            )
        finally:
            content = None
            gc.collect()


def _commit_downloads(files, keep_backups=False):
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

    if not keep_backups:
        for path, had_existing in committed:
            if had_existing:
                _remove_if_exists(_backup_name(path))


def _trial_state(version, build_epoch_utc, files, pending, previous_state):
    changed_files = []
    for file_info in pending:
        path = file_info["path"]
        changed_files.append(
            {
                "path": path,
                "had_existing": _exists(path),
                "size": file_info["size"],
                "sha256": file_info["sha256"],
            }
        )
    state = {
        "status": TRIAL_COMMIT,
        "version": version,
        "build_epoch_utc": build_epoch_utc,
        "files": [
            {"path": item["path"], "sha256": item["sha256"], "size": item["size"]}
            for item in files
        ],
        "changed_files": changed_files,
        "trial_boots": 0,
    }
    if isinstance(previous_state, dict) and previous_state:
        state["previous_state"] = previous_state
    return state


def _restore_trial_backups(state):
    changed_files = state.get("changed_files") or ()
    for item in reversed(changed_files):
        path = item.get("path")
        if not path:
            continue
        _remove_if_exists(path)
        if item.get("had_existing") and _exists(_backup_name(path)):
            os.rename(_backup_name(path), path)


def _remove_trial_backups(state):
    for item in state.get("changed_files") or ():
        path = item.get("path")
        if path:
            _remove_if_exists(_backup_name(path))


def _remove_trial_temps(state):
    for item in state.get("changed_files") or ():
        path = item.get("path")
        if path:
            _remove_if_exists(_temp_name(path))


def _remove_download_temps(files):
    for file_info in files or ():
        path = file_info.get("path")
        if path:
            _remove_if_exists(_temp_name(path))


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


def _write_confirmed_state(version, build_epoch_utc, files):
    state = {
        "status": CONFIRMED,
        "version": version,
        "build_epoch_utc": build_epoch_utc,
        "files": [
            {"path": item["path"], "sha256": item["sha256"], "size": item["size"]}
            for item in files
        ],
    }
    _write_json(STATE_FILE, state)


def _is_bad_version(version):
    if not version:
        return False
    bad = _read_json(BAD_VERSIONS_FILE, {})
    for item in bad.get("bad", ()):
        if item.get("version") == version:
            return True
    return False


def _mark_bad_version(state, reason):
    version = str(state.get("version") or "")
    if not version:
        return
    bad = _read_json(BAD_VERSIONS_FILE, {})
    entries = []
    for item in bad.get("bad", ()):
        if item.get("version") != version:
            entries.append(item)
    entries.append(
        {
            "version": version,
            "build_epoch_utc": _as_int(state.get("build_epoch_utc"), 0),
            "reason": str(reason or "unknown"),
        }
    )
    if len(entries) > MAX_BAD_VERSIONS:
        entries = entries[-MAX_BAD_VERSIONS:]
    _write_json(BAD_VERSIONS_FILE, {"bad": entries})


def _record_pretrial_failure(version, build_epoch_utc, error):
    failures = _read_json(PRETRIAL_FAILURES_FILE, {})
    entries = []
    current = None
    for item in failures.get("failures", ()):
        if item.get("version") == version:
            current = item
        else:
            entries.append(item)

    count = _as_int(current.get("count"), 0) + 1 if isinstance(current, dict) else 1
    record = {
        "version": version,
        "build_epoch_utc": build_epoch_utc,
        "count": count,
        "last_error": str(error or "unknown")[:96],
    }
    entries.append(record)
    if len(entries) > MAX_PRETRIAL_FAILURE_RECORDS:
        entries = entries[-MAX_PRETRIAL_FAILURE_RECORDS:]
    _write_json(PRETRIAL_FAILURES_FILE, {"failures": entries})

    marked_bad = False
    if count >= MAX_PRETRIAL_FAILURES:
        _mark_bad_version(
            {"version": version, "build_epoch_utc": build_epoch_utc},
            "pretrial_failure",
        )
        marked_bad = True
    return {"count": count, "marked_bad": marked_bad}


def _clear_pretrial_failure(version):
    failures = _read_json(PRETRIAL_FAILURES_FILE, {})
    entries = []
    changed = False
    for item in failures.get("failures", ()):
        if item.get("version") == version:
            changed = True
        else:
            entries.append(item)
    if not changed:
        return
    if entries:
        _write_json(PRETRIAL_FAILURES_FILE, {"failures": entries})
    else:
        _remove_if_exists(PRETRIAL_FAILURES_FILE)


def _ensure_free_space(files):
    new_bytes = _sum_file_sizes(files)
    backup_bytes = 0
    for file_info in files:
        path = file_info["path"]
        if _exists(path):
            try:
                backup_bytes += _file_size(path)
            except OSError:
                pass
    required = new_bytes + backup_bytes + MIN_FREE_AFTER_OTA_BYTES

    try:
        stat = os.statvfs("/")
        free_bytes = int(stat[0]) * int(stat[3])
    except Exception:
        return {
            "checked": False,
            "pending_bytes": new_bytes,
            "backup_bytes": backup_bytes,
            "required_bytes": required,
        }

    if free_bytes < required:
        raise OtaError("not enough free storage for rollback-safe OTA")
    return {
        "checked": True,
        "free_bytes": free_bytes,
        "pending_bytes": new_bytes,
        "backup_bytes": backup_bytes,
        "required_bytes": required,
    }


def _sum_file_sizes(files):
    total = 0
    for file_info in files or ():
        total += int(file_info.get("size", 0))
    return total


def _read_json(path, default):
    try:
        with open(path, "r") as handle:
            return json.loads(handle.read())
    except Exception:
        return default


def _write_json(path, value):
    temp = path + ".new"
    with open(temp, "w") as handle:
        handle.write(json.dumps(value))
    _remove_if_exists(path)
    os.rename(temp, path)


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


def _log_telemetry(telemetry, event, **fields):
    if telemetry is None:
        return
    try:
        telemetry.log(event, **fields)
    except Exception:
        pass


def _ticks_ms():
    try:
        return time.ticks_ms()
    except Exception:
        pass
    try:
        return int(time.time() * 1000)
    except Exception:
        return 0


def _ticks_diff(end, start):
    try:
        return time.ticks_diff(end, start)
    except Exception:
        return int(end) - int(start)


def _elapsed_ms(start):
    return _ticks_diff(_ticks_ms(), start)


def _short_error(exc):
    return str(exc or "unknown")[:96]
