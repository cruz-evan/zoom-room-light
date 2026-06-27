import hashlib
import importlib.util
import json
import sys
import types
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FakeResponse:
    def __init__(self, *, json_data=None, content=b"", status_code=200):
        self._json_data = json_data
        self.content = content
        self.status_code = status_code
        self.closed = False

    def json(self):
        return self._json_data

    def close(self):
        self.closed = True


class FakeRequests:
    def __init__(self, responses):
        self.responses = responses
        self.urls = []

    def get(self, url, headers=None, timeout=None):
        self.urls.append(url)
        response = self.responses[url]
        if isinstance(response, Exception):
            raise response
        return response


class ResetCalled(RuntimeError):
    pass


class FakeTime:
    def __init__(self):
        self.sleeps = []

    def sleep_ms(self, milliseconds):
        self.sleeps.append(milliseconds)


class FakeTelemetry:
    def __init__(self):
        self.events = []

    def log(self, event, **fields):
        self.events.append((event, fields))


def load_ota_client(monkeypatch, requests, *, installed_build_epoch):
    device_dir = PROJECT_ROOT / "device"
    module_name = "ota_client_under_test"
    spec = importlib.util.spec_from_file_location(module_name, device_dir / "ota_client.py")
    module = importlib.util.module_from_spec(spec)

    monkeypatch.setitem(
        sys.modules,
        "build_info",
        types.SimpleNamespace(BUILD_EPOCH_UTC=installed_build_epoch),
    )
    monkeypatch.setitem(
        sys.modules,
        "machine",
        types.SimpleNamespace(reset=lambda: (_ for _ in ()).throw(ResetCalled())),
    )
    monkeypatch.setitem(sys.modules, "urequests", requests)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    module.time = FakeTime()
    return module


def manifest_for(path, content, *, build_epoch_utc, version="test-version", url="https://ota.test/file.py"):
    return {
        "schema": 1,
        "version": version,
        "build_epoch_utc": build_epoch_utc,
        "files": [
            {
                "path": path,
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "url": url,
            }
        ],
    }


def manifest_for_files(files, *, build_epoch_utc, version="test-version"):
    return {
        "schema": 1,
        "version": version,
        "build_epoch_utc": build_epoch_utc,
        "files": [
            {
                "path": path,
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "url": url,
            }
            for path, content, url in files
        ],
    }


def test_ota_skips_file_diff_when_manifest_build_is_not_newer(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "main.py").write_bytes(b"newer usb build\n")

    manifest_url = "https://ota.test/manifest.json"
    file_url = "https://ota.test/main.py"
    requests = FakeRequests(
        {
            manifest_url: FakeResponse(
                json_data=manifest_for(
                    "main.py",
                    b"older ota build\n",
                    build_epoch_utc=99,
                    url=file_url,
                )
            ),
            file_url: AssertionError("stale OTA should not download firmware files"),
        }
    )
    module = load_ota_client(monkeypatch, requests, installed_build_epoch=100)

    status = module.check_for_update(manifest_url)

    assert status == "stale"
    assert requests.urls == [manifest_url]
    assert (tmp_path / "main.py").read_bytes() == b"newer usb build\n"
    assert not (tmp_path / module.STATE_FILE).exists()


def test_ota_applies_file_diff_when_manifest_build_is_newer(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "main.py").write_bytes(b"older usb build\n")

    manifest_url = "https://ota.test/manifest.json"
    file_url = "https://ota.test/main.py"
    new_content = b"newer ota build\n"
    requests = FakeRequests(
        {
            manifest_url: FakeResponse(
                json_data=manifest_for(
                    "main.py",
                    new_content,
                    build_epoch_utc=101,
                    url=file_url,
                )
            ),
            file_url: FakeResponse(content=new_content),
        }
    )
    module = load_ota_client(monkeypatch, requests, installed_build_epoch=100)
    telemetry = FakeTelemetry()

    try:
        module.check_for_update(manifest_url, telemetry=telemetry)
    except ResetCalled:
        pass
    else:
        raise AssertionError("newer OTA should reset after applying files")

    assert requests.urls == [manifest_url, file_url]
    assert (tmp_path / "main.py").read_bytes() == new_content
    state = json.loads((tmp_path / module.STATE_FILE).read_text(encoding="utf-8"))
    assert state["status"] == module.TRIAL_PENDING
    assert state["build_epoch_utc"] == 101
    assert state["version"] == "test-version"
    assert state["changed_files"] == [
        {
            "path": "main.py",
            "had_existing": True,
            "size": len(new_content),
            "sha256": hashlib.sha256(new_content).hexdigest(),
        }
    ]
    assert (tmp_path / "main.py.bak").read_bytes() == b"older usb build\n"
    assert module.time.sleeps == [250]
    event_names = [event for event, _fields in telemetry.events]
    assert event_names == [
        "ota_pretrial_manifest_fetch_start",
        "ota_pretrial_manifest_fetch_done",
        "ota_pretrial_decision",
        "ota_pretrial_space_check_done",
        "ota_pretrial_file_download_start",
        "ota_pretrial_file_download_done",
        "ota_pretrial_state_write_start",
        "ota_pretrial_state_write_done",
        "ota_pretrial_commit_start",
        "ota_pretrial_commit_done",
        "ota_pretrial_state_write_start",
        "ota_pretrial_staged",
    ]
    assert telemetry.events[2][1]["status"] == "update_available"
    assert telemetry.events[3][1]["pending_bytes"] == len(new_content)
    assert telemetry.events[4][1]["path"] == "main.py"
    assert telemetry.events[-1][1]["status"] == module.TRIAL_PENDING


def test_prepare_trial_boot_marks_candidate_running(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    module = load_ota_client(monkeypatch, FakeRequests({}), installed_build_epoch=100)
    state = {
        "status": module.TRIAL_PENDING,
        "version": "test-version",
        "build_epoch_utc": 101,
        "files": [],
        "changed_files": [],
        "trial_boots": 0,
    }
    (tmp_path / module.STATE_FILE).write_text(json.dumps(state), encoding="utf-8")

    assert module.prepare_trial_boot() == "running"

    updated = json.loads((tmp_path / module.STATE_FILE).read_text(encoding="utf-8"))
    assert updated["status"] == module.TRIAL_RUNNING
    assert updated["trial_boots"] == 1


def test_confirm_trial_boot_removes_backups_and_marks_confirmed(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    content = b"newer ota build\n"
    module = load_ota_client(monkeypatch, FakeRequests({}), installed_build_epoch=101)
    (tmp_path / "main.py").write_bytes(content)
    (tmp_path / "main.py.bak").write_bytes(b"older usb build\n")
    state = {
        "status": module.TRIAL_RUNNING,
        "version": "test-version",
        "build_epoch_utc": 101,
        "files": [
            {
                "path": "main.py",
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        ],
        "changed_files": [{"path": "main.py", "had_existing": True}],
        "trial_boots": 1,
    }
    (tmp_path / module.STATE_FILE).write_text(json.dumps(state), encoding="utf-8")

    assert module.confirm_trial_boot() == "confirmed"

    assert not (tmp_path / "main.py.bak").exists()
    state = json.loads((tmp_path / module.STATE_FILE).read_text(encoding="utf-8"))
    assert state["status"] == module.CONFIRMED
    assert state["version"] == "test-version"


def test_interrupted_trial_rolls_back_and_marks_version_bad(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    module = load_ota_client(monkeypatch, FakeRequests({}), installed_build_epoch=101)
    (tmp_path / "main.py").write_bytes(b"newer ota build\n")
    (tmp_path / "main.py.bak").write_bytes(b"older usb build\n")
    previous_state = {
        "status": module.CONFIRMED,
        "version": "old-version",
        "build_epoch_utc": 100,
        "files": [],
    }
    state = {
        "status": module.TRIAL_RUNNING,
        "version": "bad-version",
        "build_epoch_utc": 101,
        "files": [],
        "changed_files": [{"path": "main.py", "had_existing": True}],
        "previous_state": previous_state,
    }
    (tmp_path / module.STATE_FILE).write_text(json.dumps(state), encoding="utf-8")

    assert module.rollback_trial_update("trial_interrupted") == "rolled_back"

    assert (tmp_path / "main.py").read_bytes() == b"older usb build\n"
    assert not (tmp_path / "main.py.bak").exists()
    state = json.loads((tmp_path / module.STATE_FILE).read_text(encoding="utf-8"))
    assert state == previous_state
    bad = json.loads((tmp_path / module.BAD_VERSIONS_FILE).read_text(encoding="utf-8"))
    assert bad["bad"] == [
        {
            "version": "bad-version",
            "build_epoch_utc": 101,
            "reason": "trial_interrupted",
        }
    ]


def test_bad_version_is_not_downloaded_again(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "main.py").write_bytes(b"older usb build\n")
    manifest_url = "https://ota.test/manifest.json"
    file_url = "https://ota.test/main.py"
    new_content = b"newer ota build\n"
    requests = FakeRequests(
        {
            manifest_url: FakeResponse(
                json_data=manifest_for(
                    "main.py",
                    new_content,
                    build_epoch_utc=101,
                    version="bad-version",
                    url=file_url,
                )
            ),
            file_url: AssertionError("bad OTA version should not download firmware files"),
        }
    )
    module = load_ota_client(monkeypatch, requests, installed_build_epoch=100)
    (tmp_path / module.BAD_VERSIONS_FILE).write_text(
        json.dumps({"bad": [{"version": "bad-version"}]}),
        encoding="utf-8",
    )

    assert module.check_for_update(manifest_url) == "bad"
    assert requests.urls == [manifest_url]


def test_pretrial_download_failure_cleans_temps_and_records_failure(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "main.py").write_bytes(b"older usb build\n")

    manifest_url = "https://ota.test/manifest.json"
    build_info_url = "https://ota.test/build_info.py"
    main_url = "https://ota.test/main.py"
    build_info_content = b"BUILD_VERSION='bad-build'\n"
    main_content = b"newer ota build\n"
    requests = FakeRequests(
        {
            manifest_url: FakeResponse(
                json_data=manifest_for_files(
                    [
                        ("build_info.py", build_info_content, build_info_url),
                        ("main.py", main_content, main_url),
                    ],
                    build_epoch_utc=101,
                    version="flaky-version",
                )
            ),
            build_info_url: FakeResponse(content=build_info_content),
            main_url: MemoryError("memory allocation failed"),
        }
    )
    module = load_ota_client(monkeypatch, requests, installed_build_epoch=100)
    telemetry = FakeTelemetry()

    try:
        module.check_for_update(manifest_url, telemetry=telemetry)
    except MemoryError:
        pass
    else:
        raise AssertionError("download failure should bubble up for telemetry")

    assert not (tmp_path / "build_info.py.new").exists()
    assert (tmp_path / "main.py").read_bytes() == b"older usb build\n"
    failures = json.loads((tmp_path / module.PRETRIAL_FAILURES_FILE).read_text(encoding="utf-8"))
    assert failures["failures"] == [
        {
            "version": "flaky-version",
            "build_epoch_utc": 101,
            "count": 1,
            "last_error": "memory allocation failed",
        }
    ]
    assert not (tmp_path / module.BAD_VERSIONS_FILE).exists()
    event_names = [event for event, _fields in telemetry.events]
    assert event_names == [
        "ota_pretrial_manifest_fetch_start",
        "ota_pretrial_manifest_fetch_done",
        "ota_pretrial_decision",
        "ota_pretrial_space_check_done",
        "ota_pretrial_file_download_start",
        "ota_pretrial_file_download_done",
        "ota_pretrial_file_download_start",
        "ota_pretrial_error",
        "ota_pretrial_failure_recorded",
    ]
    assert telemetry.events[-2][1]["phase"] == "download"
    assert telemetry.events[-2][1]["error"] == "memory allocation failed"
    assert telemetry.events[-1][1]["count"] == 1
    assert telemetry.events[-1][1]["marked_bad"] is False


def test_repeated_pretrial_failures_mark_version_bad(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "main.py").write_bytes(b"older usb build\n")

    manifest_url = "https://ota.test/manifest.json"
    file_url = "https://ota.test/main.py"
    new_content = b"newer ota build\n"
    requests = FakeRequests(
        {
            manifest_url: FakeResponse(
                json_data=manifest_for(
                    "main.py",
                    new_content,
                    build_epoch_utc=101,
                    version="flaky-version",
                    url=file_url,
                )
            ),
            file_url: MemoryError("memory allocation failed"),
        }
    )
    module = load_ota_client(monkeypatch, requests, installed_build_epoch=100)

    for _ in range(module.MAX_PRETRIAL_FAILURES):
        try:
            module.check_for_update(manifest_url)
        except MemoryError:
            pass
        else:
            raise AssertionError("download failure should bubble up for telemetry")

    bad = json.loads((tmp_path / module.BAD_VERSIONS_FILE).read_text(encoding="utf-8"))
    assert bad["bad"] == [
        {
            "version": "flaky-version",
            "build_epoch_utc": 101,
            "reason": "pretrial_failure",
        }
    ]

    requests.responses[file_url] = AssertionError("bad version should not download again")
    assert module.check_for_update(manifest_url) == "bad"
