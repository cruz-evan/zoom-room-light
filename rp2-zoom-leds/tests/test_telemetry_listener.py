import datetime as dt

from host.telemetry_listener import format_event


def test_format_event_includes_resource_fields():
    payload = {
        "event": "resource_sample",
        "device": "board-room-a",
        "seq": 7,
        "warning": True,
        "warnings": "cpu,memory",
        "cpu_busy_pct": 82.5,
        "cpu_peak_pct": 91,
        "loop_active_avg_ms": 19.5,
        "loop_active_max_ms": 80,
        "loop_over_budget": 3,
        "heap_free_bytes": 18000,
        "heap_free_pct": 12.5,
        "temp_c": 42.1,
        "freq_mhz": 125.0,
    }

    line = format_event(
        payload,
        ("192.168.1.55", 51123),
        dt.datetime(2026, 6, 5, 12, 0, tzinfo=dt.timezone.utc),
    )

    assert "board-room-a#7 resource_sample" in line
    assert "warning=True" in line
    assert "warnings=cpu,memory" in line
    assert "cpu_busy_pct=82.5" in line
    assert "heap_free_bytes=18000" in line
    assert "temp_c=42.1" in line
