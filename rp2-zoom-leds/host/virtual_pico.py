from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEVICE_DIR = PROJECT_ROOT / "device"
OTA_MIN_FREE_AFTER_BYTES = 64 * 1024
DEFAULT_FLASH_BYTES = 2 * 1024 * 1024
DEFAULT_MICROPYTHON_BYTES = 768 * 1024
DEFAULT_FS_FILE_OVERHEAD_BYTES = 256
OTA_STATE_ESTIMATE_BYTES = 4096
OTA_BAD_RECORD_ESTIMATE_BYTES = 512
OTA_EXCLUDED_FILES = {"secrets.example.py", "build_info.py", "boot.py", "ota_client.py"}
STARTING_SOON_SCRAMBLE_FRAMES = 4

DEFAULT_SEQUENCE = (
    ("solid white", {"mode": "solid", "rgb": [255, 255, 255]}),
    ("pulse blue", {"mode": "pulse", "rgb": [0, 120, 255], "speed": 0.8}),
    ("meeting idle", {"mode": "meeting", "active": False, "participants": 0}),
    ("meeting solo", {"mode": "meeting", "active": True, "participants": 1}),
    ("meeting small", {"mode": "meeting", "active": True, "participants": 4}),
    ("meeting busy", {"mode": "meeting", "active": True, "participants": 8}),
    ("starting soon", {"mode": "meeting_status", "state": "starting_soon", "minutes": 5}),
    ("in progress", {"mode": "meeting_status", "state": "in_progress"}),
    ("ending soon", {"mode": "meeting_status", "state": "ending_soon", "minutes": 2}),
    ("off", {"mode": "off"}),
)

STARTING_SOON_COUNTDOWN_SEQUENCE = (
    (
        "starting soon 15m",
        {
            "mode": "meeting_status",
            "state": "starting_soon",
            "minutes": 15,
            "threshold": 15,
            "seconds_until_expected_state_change": 900,
        },
    ),
    (
        "starting soon 10m",
        {
            "mode": "meeting_status",
            "state": "starting_soon",
            "minutes": 15,
            "threshold": 15,
            "seconds_until_expected_state_change": 600,
        },
    ),
    (
        "starting soon 7m30s",
        {
            "mode": "meeting_status",
            "state": "starting_soon",
            "minutes": 15,
            "threshold": 15,
            "seconds_until_expected_state_change": 450,
        },
    ),
    (
        "starting soon 5m",
        {
            "mode": "meeting_status",
            "state": "starting_soon",
            "minutes": 15,
            "threshold": 15,
            "seconds_until_expected_state_change": 300,
        },
    ),
    (
        "starting soon 2m",
        {
            "mode": "meeting_status",
            "state": "starting_soon",
            "minutes": 15,
            "threshold": 15,
            "seconds_until_expected_state_change": 120,
        },
    ),
    (
        "starting soon start",
        {
            "mode": "meeting_status",
            "state": "starting_soon",
            "minutes": 15,
            "threshold": 15,
            "seconds_until_expected_state_change": 0,
        },
    ),
)

SEQUENCES = {
    "all": DEFAULT_SEQUENCE,
    "startup": DEFAULT_SEQUENCE,
    "meeting-status": tuple(item for item in DEFAULT_SEQUENCE if item[1].get("mode") == "meeting_status"),
    "starting-soon": (("starting soon", {"mode": "meeting_status", "state": "starting_soon", "minutes": 5}),),
    "starting-soon-countdown": STARTING_SOON_COUNTDOWN_SEQUENCE,
    "in-progress": (("in progress", {"mode": "meeting_status", "state": "in_progress"}),),
    "ending-soon": (("ending soon", {"mode": "meeting_status", "state": "ending_soon", "minutes": 2}),),
    "pulse": (("pulse blue", {"mode": "pulse", "rgb": [0, 120, 255], "speed": 0.8}),),
}


def _round_up(value, multiple):
    if multiple <= 1:
        return int(value)
    return ((int(value) + multiple - 1) // multiple) * multiple


def _stored_size(size, overhead):
    return int(size) + int(overhead)


def _device_python_files(device_dir):
    return sorted(Path(device_dir).glob("*.py"))


def estimate_ota_storage(args):
    files = _device_python_files(args.device_dir)
    installed_files = [
        path
        for path in files
        if path.name != "secrets.example.py"
    ]
    ota_files = [
        path
        for path in files
        if path.name not in OTA_EXCLUDED_FILES and path.name != "secrets.py"
    ]
    ota_file_sizes = {path.name: path.stat().st_size for path in ota_files}
    installed_file_sizes = {path.name: path.stat().st_size for path in installed_files}
    ota_file_sizes["build_info.py"] = args.ota_build_info_bytes

    # OTA updates all bundled app files conservatively. On-device code only
    # backs up files that actually differ, but this peak model assumes every
    # OTA payload file is pending so lighting edits do not hide storage risk.
    pending_bytes = sum(_stored_size(size, args.fs_file_overhead_bytes) for size in ota_file_sizes.values())
    backup_bytes = sum(
        _stored_size(installed_file_sizes.get(name, 0), args.fs_file_overhead_bytes)
        for name in ota_file_sizes
        if name in installed_file_sizes
    )
    installed_app_bytes = sum(
        _stored_size(size, args.fs_file_overhead_bytes) for size in installed_file_sizes.values()
    )
    installed_after_candidate_bytes = installed_app_bytes
    for name, new_size in ota_file_sizes.items():
        old_size = installed_file_sizes.get(name)
        if old_size is None:
            installed_after_candidate_bytes += _stored_size(new_size, args.fs_file_overhead_bytes)
        else:
            installed_after_candidate_bytes += (
                _stored_size(new_size, args.fs_file_overhead_bytes)
                - _stored_size(old_size, args.fs_file_overhead_bytes)
            )

    filesystem_bytes = max(0, args.flash_bytes - args.micropython_bytes)
    other_fs_bytes = max(0, args.fs_used_bytes)
    base_used_bytes = other_fs_bytes + installed_app_bytes
    free_before_bytes = filesystem_bytes - base_used_bytes
    required_extra_bytes = pending_bytes + backup_bytes + OTA_MIN_FREE_AFTER_BYTES + OTA_STATE_ESTIMATE_BYTES
    peak_trial_used_bytes = (
        other_fs_bytes
        + installed_after_candidate_bytes
        + pending_bytes
        + backup_bytes
        + OTA_STATE_ESTIMATE_BYTES
    )
    staged_trial_used_bytes = (
        other_fs_bytes
        + installed_after_candidate_bytes
        + backup_bytes
        + OTA_STATE_ESTIMATE_BYTES
    )
    confirmed_used_bytes = other_fs_bytes + installed_after_candidate_bytes + OTA_STATE_ESTIMATE_BYTES
    rolled_back_used_bytes = other_fs_bytes + installed_app_bytes + OTA_BAD_RECORD_ESTIMATE_BYTES

    failures = []
    if filesystem_bytes <= 0:
        failures.append("MicroPython firmware reserve is larger than board flash")
    if free_before_bytes < required_extra_bytes:
        failures.append(
            "OTA peak requires %s free bytes but estimate has %s"
            % (required_extra_bytes, free_before_bytes)
        )
    if filesystem_bytes - staged_trial_used_bytes < OTA_MIN_FREE_AFTER_BYTES:
        failures.append("trial mode would leave less than %s filesystem bytes free" % OTA_MIN_FREE_AFTER_BYTES)

    return {
        "flash_bytes": args.flash_bytes,
        "micropython_firmware_bytes": args.micropython_bytes,
        "filesystem_bytes_estimate": filesystem_bytes,
        "fs_file_overhead_bytes": args.fs_file_overhead_bytes,
        "other_fs_used_bytes": other_fs_bytes,
        "installed_app_bytes": installed_app_bytes,
        "ota_file_count": len(ota_file_sizes),
        "ota_payload_bytes": pending_bytes,
        "ota_backup_bytes": backup_bytes,
        "ota_state_estimate_bytes": OTA_STATE_ESTIMATE_BYTES,
        "ota_required_free_before_bytes": required_extra_bytes,
        "free_before_ota_estimate_bytes": free_before_bytes,
        "peak_trial_used_bytes": peak_trial_used_bytes,
        "free_at_peak_trial_estimate_bytes": filesystem_bytes - peak_trial_used_bytes,
        "staged_trial_used_bytes": staged_trial_used_bytes,
        "free_in_staged_trial_estimate_bytes": filesystem_bytes - staged_trial_used_bytes,
        "confirmed_used_bytes": confirmed_used_bytes,
        "free_after_confirm_estimate_bytes": filesystem_bytes - confirmed_used_bytes,
        "rolled_back_bad_build_used_bytes": rolled_back_used_bytes,
        "free_after_bad_rollback_estimate_bytes": filesystem_bytes - rolled_back_used_bytes,
        "bad_build_candidate_deleted_on_rollback": True,
        "minimum_free_after_ota_bytes": OTA_MIN_FREE_AFTER_BYTES,
        "files": dict(sorted(ota_file_sizes.items())),
        "failures": failures,
    }


class VirtualPicoFailure(RuntimeError):
    pass


class FakePin:
    OUT = 1

    def __init__(self, pin, mode):
        self.pin = pin
        self.mode = mode
        self.last_value = 0

    def value(self, value):
        self.last_value = 1 if value else 0


class FakeMachine:
    WDT_RESET = 3
    PWRON_RESET = 1

    def __init__(self, budget):
        self.budget = budget
        self.Pin = FakePin
        self.reset_count = 0
        self._reset_cause = self.PWRON_RESET

    def reset(self):
        self.reset_count += 1
        self.budget.reset_requested = True
        raise VirtualPicoFailure("machine.reset() requested")

    def reset_cause(self):
        return self._reset_cause

    def freq(self):
        return self.budget.cpu_hz

    def unique_id(self):
        return b"\xe6d0\xa6K*\x8d2"


class BudgetedNeoPixel:
    ORDER = (1, 0, 2)
    active_budget = None

    def __init__(self, pin, count):
        self.pin = pin
        self.count = int(count)
        self.buf = bytearray(self.count * 3)
        self.set_count = 0
        self.fill_count = 0
        self.write_count = 0
        self.last_write_ms = None
        self.writes = []
        if self.active_budget is not None:
            self.active_budget.reserve_heap(len(self.buf), "neopixel_buffer")

    def __setitem__(self, index, value):
        self.set_count += 1
        if self.active_budget is not None:
            self.active_budget.charge_us(self.active_budget.pixel_set_us, "pixel_set")
        offset = index * 3
        self.buf[offset] = int(value[1])
        self.buf[offset + 1] = int(value[0])
        self.buf[offset + 2] = int(value[2])

    def fill(self, color):
        self.fill_count += 1
        if self.active_budget is not None:
            self.active_budget.charge_us(
                self.active_budget.pixel_fill_us + (self.count * self.active_budget.pixel_fill_per_led_us),
                "pixel_fill",
            )
        encoded = bytes((int(color[1]), int(color[0]), int(color[2])))
        self.buf[:] = encoded * self.count

    def write(self):
        self.write_count += 1
        if self.active_budget is not None:
            self.active_budget.charge_us(
                self.active_budget.neopixel_write_base_us
                + (self.count * self.active_budget.neopixel_write_us_per_led),
                "neopixel_write",
            )
            self.last_write_ms = self.active_budget.now_ms
        self.writes.append(bytes(self.buf))


@dataclass
class LoopSample:
    label: str
    active_ms: float
    total_ms: float
    wrote_pixels: bool
    heap_free_bytes: int
    reasons: dict[str, float]


@dataclass
class VirtualBudget:
    cpu_hz: int = 125_000_000
    heap_bytes: int = 264 * 1024
    min_free_bytes: int = 24_000
    loop_delay_ms: int = 34
    max_loop_ms: float | None = None
    watchdog_timeout_ms: int = 8000
    state_poll_seconds: int = 5
    max_state_poll_lag_ms: int = 250
    python_loop_base_us: float = 180.0
    apply_base_us: float = 350.0
    tick_base_us: float = 90.0
    normalize_command_us: float = 100.0
    pixel_set_us: float = 3.0
    pixel_fill_us: float = 35.0
    pixel_fill_per_led_us: float = 0.45
    pixel_buffer_copy_us_per_led: float = 0.75
    pattern_math_us_per_led: float = 2.8
    pattern_math_us_per_active_led: float = 18.0
    solid_math_us: float = 80.0
    neopixel_write_base_us: float = 80.0
    neopixel_write_us_per_led: float = 30.0
    now_ms: int = 0
    reset_requested: bool = False
    reserved_heap: int = 52_000
    transient_heap: int = 0
    min_heap_free_seen: int = field(init=False)
    active_us: float = 0
    reason_us: dict[str, float] = field(default_factory=dict)
    samples: list[LoopSample] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    next_state_poll_ms: int = field(init=False)
    max_state_poll_lag_seen_ms: int = 0

    def __post_init__(self):
        self.max_loop_ms = self.loop_delay_ms if self.max_loop_ms is None else self.max_loop_ms
        self.min_heap_free_seen = self.heap_bytes - self.reserved_heap
        self.next_state_poll_ms = self.state_poll_seconds * 1000

    def ticks_ms(self):
        return self.now_ms

    @staticmethod
    def ticks_diff(now, start):
        return int(now) - int(start)

    @staticmethod
    def ticks_add(ticks, delta):
        return int(ticks) + int(delta)

    def sleep_ms(self, milliseconds):
        self.now_ms += max(0, int(milliseconds))

    def start_loop(self):
        self.active_us = 0
        self.transient_heap = 0
        self.reason_us = {}
        self.charge_us(self.python_loop_base_us, "loop_base")

    def charge_us(self, amount, reason):
        amount = max(0.0, float(amount))
        self.active_us += amount
        self.reason_us[reason] = self.reason_us.get(reason, 0.0) + amount

    def reserve_heap(self, amount, reason):
        self.reserved_heap += max(0, int(amount))
        self._observe_heap(reason)

    def alloc_temp(self, amount, reason):
        self.transient_heap += max(0, int(amount))
        self._observe_heap(reason)

    def _observe_heap(self, reason):
        free = self.heap_free_bytes()
        self.min_heap_free_seen = min(self.min_heap_free_seen, free)
        if free < self.min_free_bytes:
            self.failures.append(
                "%s left %s heap bytes below minimum %s" % (reason, free, self.min_free_bytes)
            )

    def heap_free_bytes(self):
        return self.heap_bytes - self.reserved_heap - self.transient_heap

    def finish_loop(self, label, wrote_pixels):
        active_ms = self.active_us / 1000.0
        total_ms = active_ms + self.loop_delay_ms
        self.samples.append(
            LoopSample(
                label=label,
                active_ms=active_ms,
                total_ms=total_ms,
                wrote_pixels=wrote_pixels,
                heap_free_bytes=self.heap_free_bytes(),
                reasons={key: value / 1000.0 for key, value in self.reason_us.items()},
            )
        )
        if active_ms > float(self.max_loop_ms):
            self.failures.append(
                "%s active loop %.2f ms exceeded budget %.2f ms"
                % (label, active_ms, float(self.max_loop_ms))
            )
        if active_ms > float(self.watchdog_timeout_ms):
            self.failures.append(
                "%s active loop %.2f ms exceeded watchdog timeout %s ms"
                % (label, active_ms, self.watchdog_timeout_ms)
            )
        self.now_ms += int(math.ceil(active_ms)) + self.loop_delay_ms
        self._observe_state_poll_lag()

    def _observe_state_poll_lag(self):
        if self.state_poll_seconds <= 0:
            return
        while self.now_ms >= self.next_state_poll_ms:
            lag = self.now_ms - self.next_state_poll_ms
            self.max_state_poll_lag_seen_ms = max(self.max_state_poll_lag_seen_ms, lag)
            if lag > self.max_state_poll_lag_ms:
                self.failures.append(
                    "state poll lag %s ms exceeded budget %s ms"
                    % (lag, self.max_state_poll_lag_ms)
                )
            self.next_state_poll_ms += self.state_poll_seconds * 1000


def _install_fake_modules(budget):
    machine = FakeMachine(budget)
    BudgetedNeoPixel.active_budget = budget
    sys.modules["machine"] = machine
    sys.modules["neopixel"] = types.SimpleNamespace(NeoPixel=BudgetedNeoPixel)
    return machine


def _patch_time_module(budget):
    import time

    time.ticks_ms = budget.ticks_ms
    time.ticks_diff = budget.ticks_diff
    time.ticks_add = budget.ticks_add
    time.sleep_ms = budget.sleep_ms
    return time


def load_led_strip(budget):
    _install_fake_modules(budget)
    _patch_time_module(budget)
    module_name = "virtual_pico_led_strip"
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(module_name, DEVICE_DIR / "led_strip.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _estimate_command_cost(budget, command, led_count, force):
    mode = command.get("mode")
    budget.charge_us(budget.normalize_command_us, "normalize_command")
    if force:
        budget.charge_us(budget.apply_base_us, "apply_command")
    else:
        budget.charge_us(budget.tick_base_us, "tick")

    if mode == "meeting_status" and command.get("state") == "starting_soon":
        budget.charge_us(budget.solid_math_us, "starting_soon_pulse_colors")
        if command.get("seconds_until_expected_state_change") is not None:
            try:
                remaining = float(command.get("seconds_until_expected_state_change"))
            except (TypeError, ValueError):
                remaining = None
            try:
                threshold_minutes = float(command.get("threshold", command.get("minutes", 15.0)))
            except (TypeError, ValueError):
                threshold_minutes = 15.0
            if remaining is not None:
                total = max(1.0, threshold_minutes * 60.0)
                blend = 1.0 - (max(0.0, remaining) / total)
                if 0.0 < blend < 1.0:
                    choice_scale = 1.0 if force else (1.0 / STARTING_SOON_SCRAMBLE_FRAMES)
                    budget.charge_us(
                        led_count * budget.pattern_math_us_per_led * choice_scale,
                        "starting_soon_mask_choice",
                    )
                    budget.charge_us(
                        led_count * budget.pixel_buffer_copy_us_per_led,
                        "starting_soon_buffer_mix",
                    )
    elif mode in ("pulse", "meeting_status") or mode == "meeting":
        budget.charge_us(budget.solid_math_us, "solid_or_pulse_math")
    elif mode in ("solid", "off"):
        budget.charge_us(budget.solid_math_us, "solid_or_pulse_math")


def _run_command(strip, budget, label, command, duration_ms, led_count):
    command_started_ms = budget.now_ms
    pixel_writes_before = strip.pixels.write_count

    budget.start_loop()
    _estimate_command_cost(budget, command, led_count, force=True)
    ok = strip.apply(command)
    wrote = strip.pixels.write_count > pixel_writes_before
    budget.finish_loop(label + " apply", wrote)
    if not ok:
        budget.failures.append("%s apply returned False" % label)

    while budget.now_ms - command_started_ms < duration_ms:
        writes_before = strip.pixels.write_count
        budget.start_loop()
        _estimate_command_cost(budget, strip.current_command, led_count, force=False)
        strip.tick()
        wrote = strip.pixels.write_count > writes_before
        budget.finish_loop(label + " tick", wrote)


def run_virtual_pico(args):
    sequence = SEQUENCES[args.sequence]
    budget = VirtualBudget(
        heap_bytes=args.heap_bytes,
        min_free_bytes=args.min_free_bytes,
        loop_delay_ms=args.loop_delay_ms,
        max_loop_ms=args.max_loop_ms,
        watchdog_timeout_ms=args.watchdog_timeout_ms,
        state_poll_seconds=args.state_poll_seconds,
        max_state_poll_lag_ms=args.max_state_poll_lag_ms,
    )
    led_strip = load_led_strip(budget)
    strip = led_strip.LedStrip(
        pin=args.pin,
        count=args.led_count,
        brightness=args.brightness,
        max_refresh_fps=args.max_fps,
    )
    duration_ms = int(args.duration_seconds * 1000)

    for label, command in sequence:
        _run_command(strip, budget, label, command, duration_ms, args.led_count)

    storage = estimate_ota_storage(args)
    return report_for(budget, strip, args, storage)


def report_for(budget, strip, args, storage):
    samples = budget.samples
    worst = max(samples, key=lambda sample: sample.active_ms) if samples else None
    writes = sum(1 for sample in samples if sample.wrote_pixels)
    elapsed_seconds = max(0.001, budget.now_ms / 1000.0)
    reason_totals = {}
    for sample in samples:
        for reason, ms in sample.reasons.items():
            reason_totals[reason] = reason_totals.get(reason, 0.0) + ms
    top_reasons = sorted(reason_totals.items(), key=lambda item: item[1], reverse=True)[:6]
    failures = list(budget.failures) + ["storage: %s" % failure for failure in storage["failures"]]

    return {
        "ok": not failures,
        "sequence": args.sequence,
        "led_count": args.led_count,
        "brightness": args.brightness,
        "max_fps": args.max_fps,
        "loop_delay_ms": args.loop_delay_ms,
        "loops": len(samples),
        "elapsed_seconds": round(elapsed_seconds, 3),
        "pixel_writes": strip.pixels.write_count,
        "writes_per_second": round(writes / elapsed_seconds, 2),
        "worst_loop_ms": round(worst.active_ms, 3) if worst else 0,
        "worst_loop_label": worst.label if worst else "",
        "avg_loop_ms": round(sum(sample.active_ms for sample in samples) / len(samples), 3)
        if samples
        else 0,
        "max_loop_budget_ms": args.max_loop_ms,
        "heap_bytes": args.heap_bytes,
        "min_free_heap_estimate_bytes": budget.min_heap_free_seen,
        "min_free_heap_required_bytes": args.min_free_bytes,
        "max_state_poll_lag_ms": budget.max_state_poll_lag_seen_ms,
        "top_costs_ms": [(reason, round(ms, 3)) for reason, ms in top_reasons],
        "ota_storage": storage,
        "failures": failures,
    }


def print_text_report(report):
    status = "PASS" if report["ok"] else "FAIL"
    print("Virtual Pico lighting run: %s" % status)
    print("  sequence: %s" % report["sequence"])
    print("  leds: %s, max_fps: %s, loop_delay_ms: %s" % (
        report["led_count"],
        report["max_fps"],
        report["loop_delay_ms"],
    ))
    print("  loops: %s, elapsed_seconds: %s" % (report["loops"], report["elapsed_seconds"]))
    print("  pixel_writes: %s, writes_per_second: %s" % (
        report["pixel_writes"],
        report["writes_per_second"],
    ))
    print("  worst_loop_ms: %s (%s)" % (report["worst_loop_ms"], report["worst_loop_label"]))
    print("  avg_loop_ms: %s" % report["avg_loop_ms"])
    print("  heap_free_min_estimate: %s / required %s" % (
        report["min_free_heap_estimate_bytes"],
        report["min_free_heap_required_bytes"],
    ))
    print("  max_state_poll_lag_ms: %s" % report["max_state_poll_lag_ms"])
    storage = report["ota_storage"]
    print("  ota_storage:")
    print("    flash_bytes: %s" % storage["flash_bytes"])
    print("    micropython_firmware_bytes: %s" % storage["micropython_firmware_bytes"])
    print("    filesystem_bytes_estimate: %s" % storage["filesystem_bytes_estimate"])
    print("    installed_app_bytes: %s" % storage["installed_app_bytes"])
    print("    ota_payload_bytes: %s" % storage["ota_payload_bytes"])
    print("    ota_backup_bytes: %s" % storage["ota_backup_bytes"])
    print("    ota_required_free_before_bytes: %s" % storage["ota_required_free_before_bytes"])
    print("    free_before_ota_estimate_bytes: %s" % storage["free_before_ota_estimate_bytes"])
    print("    free_at_peak_trial_estimate_bytes: %s" % storage["free_at_peak_trial_estimate_bytes"])
    print("    free_in_staged_trial_estimate_bytes: %s" % storage["free_in_staged_trial_estimate_bytes"])
    print("    free_after_confirm_estimate_bytes: %s" % storage["free_after_confirm_estimate_bytes"])
    print("    free_after_bad_rollback_estimate_bytes: %s" % storage["free_after_bad_rollback_estimate_bytes"])
    print("    bad_build_candidate_deleted_on_rollback: %s" % (
        "yes" if storage["bad_build_candidate_deleted_on_rollback"] else "no"
    ))
    if report["top_costs_ms"]:
        print("  top_costs_ms:")
        for reason, ms in report["top_costs_ms"]:
            print("    %s: %s" % (reason, ms))
    if report["failures"]:
        print("  failures:")
        for failure in report["failures"]:
            print("    - %s" % failure)


def build_parser():
    parser = argparse.ArgumentParser(
        description="Run the RP2 LED routines on a budgeted virtual Pico."
    )
    parser.add_argument(
        "--sequence",
        choices=sorted(SEQUENCES),
        default="all",
        help="Lighting sequence to run. Use all to cycle through every built-in routine.",
    )
    parser.add_argument("--list-sequences", action="store_true")
    parser.add_argument("--duration-seconds", type=float, default=3.0)
    parser.add_argument("--led-count", type=int, default=144)
    parser.add_argument("--pin", type=int, default=0)
    parser.add_argument("--brightness", type=float, default=0.12)
    parser.add_argument("--max-fps", type=int, default=30)
    parser.add_argument("--loop-delay-ms", type=int, default=34)
    parser.add_argument("--max-loop-ms", type=float, default=34.0)
    parser.add_argument("--watchdog-timeout-ms", type=int, default=8000)
    parser.add_argument("--state-poll-seconds", type=int, default=5)
    parser.add_argument("--max-state-poll-lag-ms", type=int, default=250)
    parser.add_argument("--heap-bytes", type=int, default=264 * 1024)
    parser.add_argument("--min-free-bytes", type=int, default=24_000)
    parser.add_argument("--device-dir", type=Path, default=DEVICE_DIR)
    parser.add_argument("--flash-bytes", type=int, default=DEFAULT_FLASH_BYTES)
    parser.add_argument("--micropython-bytes", type=int, default=DEFAULT_MICROPYTHON_BYTES)
    parser.add_argument(
        "--fs-used-bytes",
        type=int,
        default=0,
        help="Estimated non-app filesystem bytes already used on the Pico.",
    )
    parser.add_argument("--fs-file-overhead-bytes", type=int, default=DEFAULT_FS_FILE_OVERHEAD_BYTES)
    parser.add_argument("--ota-build-info-bytes", type=int, default=128)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.list_sequences:
        for name in sorted(SEQUENCES):
            print(name)
        return 0

    report = run_virtual_pico(args)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_text_report(report)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
