from device import resource_monitor


class FakeTelemetry:
    def __init__(self):
        self.events = []

    def log(self, event, **fields):
        self.events.append((event, fields))


class FakeGc:
    def __init__(self, free=12000, allocated=48000):
        self.free = free
        self.allocated = allocated
        self.collected = False

    def collect(self):
        self.collected = True

    def mem_free(self):
        return self.free

    def mem_alloc(self):
        return self.allocated


def test_resource_monitor_reports_loop_and_heap_pressure(monkeypatch):
    now = 0
    fake_gc = FakeGc(free=12000, allocated=48000)
    telemetry = FakeTelemetry()

    monkeypatch.setattr(resource_monitor, "gc", fake_gc)
    monkeypatch.setattr(resource_monitor, "machine", None)

    monitor = resource_monitor.ResourceMonitor(
        telemetry,
        enabled=True,
        sample_seconds=1,
        loop_sleep_ms=10,
        cpu_warn_percent=50,
        min_free_bytes=24000,
        gc_collect=True,
        include_temp=False,
        ticks_ms=lambda: now,
    )

    monitor.observe_loop(5)
    assert telemetry.events == []

    now = 1000
    monitor.observe_loop(40)

    assert len(telemetry.events) == 1
    event, fields = telemetry.events[0]
    assert event == "resource_sample"
    assert fields["loops"] == 2
    assert fields["loop_active_avg_ms"] == 22.5
    assert fields["loop_active_max_ms"] == 40
    assert fields["loop_over_budget"] == 1
    assert fields["cpu_busy_pct"] == 69.2
    assert fields["cpu_peak_pct"] == 80
    assert fields["heap_free_bytes"] == 12000
    assert fields["heap_alloc_bytes"] == 48000
    assert fields["heap_total_bytes"] == 60000
    assert fields["heap_free_pct"] == 20.0
    assert fields["warning"] is True
    assert fields["warnings"] == "cpu,memory,loop_over_budget"
    assert "gc_collect_ms" in fields
    assert fake_gc.collected is True


def test_resource_monitor_resets_interval_after_sample(monkeypatch):
    now = 0
    telemetry = FakeTelemetry()

    monkeypatch.setattr(resource_monitor, "gc", FakeGc(free=60000, allocated=40000))
    monkeypatch.setattr(resource_monitor, "machine", None)

    monitor = resource_monitor.ResourceMonitor(
        telemetry,
        enabled=True,
        sample_seconds=1,
        loop_sleep_ms=20,
        cpu_warn_percent=90,
        min_free_bytes=24000,
        include_temp=False,
        ticks_ms=lambda: now,
    )

    now = 1000
    monitor.observe_loop(2)
    now = 1999
    monitor.observe_loop(2)
    assert len(telemetry.events) == 1

    now = 2000
    monitor.observe_loop(2)
    assert len(telemetry.events) == 2
    assert telemetry.events[1][1]["loops"] == 2


def test_disabled_resource_monitor_does_not_log():
    telemetry = FakeTelemetry()
    monitor = resource_monitor.ResourceMonitor(telemetry, enabled=False, sample_seconds=1)

    monitor.observe_loop(999)
    monitor.log_sample()

    assert telemetry.events == []
