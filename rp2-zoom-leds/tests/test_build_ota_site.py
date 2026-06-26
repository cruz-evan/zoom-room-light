import hashlib
import importlib.util
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_build_module():
    module_path = PROJECT_ROOT / "scripts" / "build_ota_site.py"
    spec = importlib.util.spec_from_file_location("build_ota_site", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_ota_site_excludes_secret_files(tmp_path):
    build_ota_site = load_build_module()
    device_dir = tmp_path / "device"
    output_dir = tmp_path / "site"
    device_dir.mkdir()

    (device_dir / "main.py").write_text("print('main')\n", encoding="utf-8")
    (device_dir / "led_strip.py").write_text("LED_COUNT = 144\n", encoding="utf-8")
    (device_dir / "secrets.py").write_text("WIFI_PASSWORD = 'nope'\n", encoding="utf-8")
    (device_dir / "secrets.example.py").write_text("WIFI_PASSWORD = ''\n", encoding="utf-8")

    manifest = build_ota_site.build_ota_site(
        device_dir=device_dir,
        output_dir=output_dir,
        base_url="https://example.com/ota/",
        version="abc123",
        build_epoch_utc=1800000000,
    )

    paths = [item["path"] for item in manifest["files"]]
    assert paths == ["build_info.py", "led_strip.py", "main.py"]
    assert not (output_dir / "firmware" / "abc123" / "secrets.py").exists()
    assert not (output_dir / "firmware" / "abc123" / "secrets.example.py").exists()


def test_build_ota_site_writes_hashes_and_urls(tmp_path):
    build_ota_site = load_build_module()
    device_dir = tmp_path / "device"
    output_dir = tmp_path / "site"
    device_dir.mkdir()

    main_data = b"print('hello ota')\n"
    (device_dir / "main.py").write_bytes(main_data)

    manifest = build_ota_site.build_ota_site(
        device_dir=device_dir,
        output_dir=output_dir,
        base_url="https://example.com/rp2-zoom-leds",
        version="feature/test build",
        build_epoch_utc=1800000000,
    )

    files_by_path = {item["path"]: item for item in manifest["files"]}
    file_info = files_by_path["main.py"]
    assert file_info == {
        "path": "main.py",
        "size": len(main_data),
        "sha256": hashlib.sha256(main_data).hexdigest(),
        "url": "https://example.com/rp2-zoom-leds/firmware/feature-test-build/main.py",
    }

    written_manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert written_manifest["version"] == "feature/test build"
    assert written_manifest["build_epoch_utc"] == 1800000000
    assert written_manifest["build_id_utc"] == "2027-01-15T08:00:00Z"
    assert written_manifest["files"] == manifest["files"]
    assert (output_dir / "firmware" / "feature-test-build" / "main.py").read_bytes() == main_data

    build_info_data = (output_dir / "firmware" / "feature-test-build" / "build_info.py").read_text(
        encoding="utf-8"
    )
    assert "BUILD_EPOCH_UTC = 1800000000" in build_info_data
    assert "BUILD_ID_UTC = '2027-01-15T08:00:00Z'" in build_info_data
    assert "BUILD_VERSION = 'feature/test build'" in build_info_data
