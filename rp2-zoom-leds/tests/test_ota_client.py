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

    try:
        module.check_for_update(manifest_url)
    except ResetCalled:
        pass
    else:
        raise AssertionError("newer OTA should reset after applying files")

    assert requests.urls == [manifest_url, file_url]
    assert (tmp_path / "main.py").read_bytes() == new_content
    state = json.loads((tmp_path / module.STATE_FILE).read_text(encoding="utf-8"))
    assert state["build_epoch_utc"] == 101
    assert state["version"] == "test-version"
    assert module.time.sleeps == [250]
