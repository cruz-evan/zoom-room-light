import importlib.util
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_render_module():
    module_path = PROJECT_ROOT / "scripts" / "render_device_secrets.py"
    spec = importlib.util.spec_from_file_location("render_device_secrets", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_render_secrets_replaces_wifi_assignments_without_touching_other_config():
    render_device_secrets = load_render_module()
    source = (
        'WIFI_SSID = "old-network"\n'
        'WIFI_PASSWORD = "old-password"\n'
        'DEVICE_ID = "board-room-a"\n'
    )

    rendered = render_device_secrets.render_secrets(
        source,
        {"WIFI_SSID": "office wifi", "WIFI_PASSWORD": "quoted'password"},
    )

    assert "WIFI_SSID = 'office wifi'" in rendered
    assert 'WIFI_PASSWORD = "old-password"' not in rendered
    assert 'WIFI_PASSWORD = "quoted\'password"' in rendered
    assert 'DEVICE_ID = "board-room-a"' in rendered


def test_render_secrets_appends_missing_assignments():
    render_device_secrets = load_render_module()

    rendered = render_device_secrets.render_secrets(
        'DEVICE_ID = "board-room-a"\n',
        {"WIFI_SSID": "office"},
    )

    assert rendered.endswith(
        '# Injected from environment during USB provisioning.\n'
        "WIFI_SSID = 'office'\n"
    )


def test_render_secrets_rejects_invalid_names():
    render_device_secrets = load_render_module()

    try:
        render_device_secrets.render_secrets("", {"wifi_ssid": "office"})
    except ValueError as exc:
        assert "Invalid secret name" in str(exc)
    else:
        raise AssertionError("Expected invalid secret name to raise ValueError")
