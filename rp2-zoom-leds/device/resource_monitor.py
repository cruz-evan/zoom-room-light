try:
    import gc
except ImportError:
    gc = None

try:
    import machine
except ImportError:
    machine = None

import time


DEFAULT_SAMPLE_SECONDS = 10
DEFAULT_CPU_WARN_PERCENT = 80
DEFAULT_MIN_FREE_BYTES = 24000

_TEMP_ADC = None


def _ticks_ms():
    if hasattr(time, "ticks_ms"):
        return time.ticks_ms()
    return int(time.monotonic() * 1000)


def _ticks_add(ticks, delta):
    if hasattr(time, "ticks_add"):
        return time.ticks_add(ticks, delta)
    return ticks + delta


def _ticks_diff(left, right):
    if hasattr(time, "ticks_diff"):
        return time.ticks_diff(left, right)
    return left - right


def _round(value, digits=1):
    try:
        return round(value, digits)
    except TypeError:
        factor = 10 ** digits
        return int(value * factor + 0.5) / factor


def _as_int(value, default, minimum=None, maximum=None):
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = int(default)
    if minimum is not None and number < minimum:
        return minimum
    if maximum is not None and number > maximum:
        return maximum
    return number


def _memory_fields(collect=False):
    fields = {}
    if gc is None:
        return fields

    if collect and hasattr(gc, "collect"):
        started = _ticks_ms()
        try:
            gc.collect()
            fields["gc_collect_ms"] = max(0, _ticks_diff(_ticks_ms(), started))
        except Exception:
            pass

    try:
        free = int(gc.mem_free())
        fields["heap_free_bytes"] = free
    except Exception:
        free = None

    try:
        allocated = int(gc.mem_alloc())
        fields["heap_alloc_bytes"] = allocated
    except Exception:
        allocated = None

    if free is not None and allocated is not None:
        total = free + allocated
        fields["heap_total_bytes"] = total
        if total > 0:
            fields["heap_free_pct"] = _round((free * 100.0) / total, 1)

    return fields


def _machine_fields(include_temp=True):
    fields = {}
    if machine is None:
        return fields

    try:
        fields["freq_hz"] = int(machine.freq())
        fields["freq_mhz"] = _round(fields["freq_hz"] / 1000000.0, 1)
    except Exception:
        pass

    if include_temp:
        temp_c = _cpu_temp_c()
        if temp_c is not None:
            fields["temp_c"] = _round(temp_c, 1)

    return fields


def _cpu_temp_c():
    global _TEMP_ADC
    if machine is None or not hasattr(machine, "ADC"):
        return None

    try:
        if _TEMP_ADC is None:
            _TEMP_ADC = machine.ADC(4)
        reading = _TEMP_ADC.read_u16()
        voltage = reading * 3.3 / 65535
        return 27 - (voltage - 0.706) / 0.001721
    except Exception:
        return None


class ResourceMonitor:
    def __init__(
        self,
        telemetry,
        enabled=False,
        sample_seconds=DEFAULT_SAMPLE_SECONDS,
        loop_sleep_ms=0,
        cpu_warn_percent=DEFAULT_CPU_WARN_PERCENT,
        min_free_bytes=DEFAULT_MIN_FREE_BYTES,
        gc_collect=False,
        include_temp=True,
        ticks_ms=None,
    ):
        self.telemetry = telemetry
        self.enabled = bool(enabled and telemetry)
        self.sample_ms = _as_int(sample_seconds, DEFAULT_SAMPLE_SECONDS, 1, 3600) * 1000
        self.loop_sleep_ms = _as_int(loop_sleep_ms, 0, 0, 60000)
        self.cpu_warn_percent = _as_int(cpu_warn_percent, DEFAULT_CPU_WARN_PERCENT, 1, 100)
        self.min_free_bytes = _as_int(min_free_bytes, DEFAULT_MIN_FREE_BYTES, 0, None)
        self.gc_collect = bool(gc_collect)
        self.include_temp = bool(include_temp)
        self._ticks_ms = ticks_ms or _ticks_ms
        now = self._ticks_ms()
        self.started_ms = now
        self.sample_started_ms = now
        self.next_sample_ms = _ticks_add(now, self.sample_ms)
        self.reset_interval()

    def reset_interval(self):
        self.loop_count = 0
        self.loop_active_total_ms = 0
        self.loop_active_max_ms = 0
        self.loop_over_budget = 0
        self.cpu_peak_pct = 0

    def observe_loop(self, active_ms, sleep_ms=None):
        if not self.enabled:
            return

        active_ms = _as_int(active_ms, 0, 0, None)
        sleep_ms = self.loop_sleep_ms if sleep_ms is None else _as_int(sleep_ms, 0, 0, None)
        total_ms = active_ms + sleep_ms
        cpu_pct = 100 if total_ms <= 0 and active_ms > 0 else 0
        if total_ms > 0:
            cpu_pct = int((active_ms * 100) / total_ms)

        self.loop_count += 1
        self.loop_active_total_ms += active_ms
        if active_ms > self.loop_active_max_ms:
            self.loop_active_max_ms = active_ms
        if active_ms > sleep_ms:
            self.loop_over_budget += 1
        if cpu_pct > self.cpu_peak_pct:
            self.cpu_peak_pct = cpu_pct

        now = self._ticks_ms()
        if _ticks_diff(now, self.next_sample_ms) >= 0:
            self.log_sample(now, sleep_ms)

    def log_sample(self, now_ms=None, sleep_ms=None):
        if not self.enabled:
            return

        now_ms = self._ticks_ms() if now_ms is None else now_ms
        sleep_ms = self.loop_sleep_ms if sleep_ms is None else _as_int(sleep_ms, 0, 0, None)
        loops = self.loop_count
        interval_ms = max(0, _ticks_diff(now_ms, self.sample_started_ms))
        active_total = self.loop_active_total_ms
        estimated_total = active_total + loops * sleep_ms

        fields = {
            "uptime_ms": max(0, _ticks_diff(now_ms, self.started_ms)),
            "interval_ms": interval_ms,
            "loops": loops,
            "loop_sleep_ms": sleep_ms,
            "loop_active_avg_ms": 0,
            "loop_active_max_ms": self.loop_active_max_ms,
            "loop_over_budget": self.loop_over_budget,
            "loop_over_budget_pct": 0,
            "cpu_busy_pct": 0,
            "cpu_peak_pct": self.cpu_peak_pct,
            "cpu_warn_pct": self.cpu_warn_percent,
            "heap_min_free_bytes": self.min_free_bytes,
        }

        if loops > 0:
            fields["loop_active_avg_ms"] = _round(active_total / float(loops), 1)
            fields["loop_over_budget_pct"] = _round((self.loop_over_budget * 100.0) / loops, 1)
        if estimated_total > 0:
            fields["cpu_busy_pct"] = _round((active_total * 100.0) / estimated_total, 1)

        fields.update(_memory_fields(self.gc_collect))
        fields.update(_machine_fields(self.include_temp))

        warnings = []
        if fields["cpu_busy_pct"] >= self.cpu_warn_percent:
            warnings.append("cpu")
        elif fields["cpu_peak_pct"] >= self.cpu_warn_percent:
            warnings.append("cpu_peak")
        if fields.get("heap_free_bytes") is not None and fields["heap_free_bytes"] < self.min_free_bytes:
            warnings.append("memory")
        if self.loop_over_budget > 0:
            warnings.append("loop_over_budget")

        fields["warning"] = bool(warnings)
        if warnings:
            fields["warnings"] = ",".join(warnings)

        self.telemetry.log("resource_sample", **fields)
        self.sample_started_ms = now_ms
        self.next_sample_ms = _ticks_add(now_ms, self.sample_ms)
        self.reset_interval()


class NullResourceMonitor:
    def observe_loop(self, active_ms, sleep_ms=None):
        pass

    def log_sample(self, now_ms=None, sleep_ms=None):
        pass


def from_config(config, telemetry):
    return ResourceMonitor(
        telemetry=telemetry,
        enabled=getattr(config, "RESOURCE_MONITOR_ENABLED", False),
        sample_seconds=getattr(config, "RESOURCE_MONITOR_SAMPLE_SECONDS", DEFAULT_SAMPLE_SECONDS),
        loop_sleep_ms=getattr(config, "LOOP_DELAY_MS", 0),
        cpu_warn_percent=getattr(config, "RESOURCE_MONITOR_CPU_WARN_PERCENT", DEFAULT_CPU_WARN_PERCENT),
        min_free_bytes=getattr(config, "RESOURCE_MONITOR_MIN_FREE_BYTES", DEFAULT_MIN_FREE_BYTES),
        gc_collect=getattr(config, "RESOURCE_MONITOR_GC_COLLECT", False),
        include_temp=getattr(config, "RESOURCE_MONITOR_INCLUDE_TEMP", True),
    )
