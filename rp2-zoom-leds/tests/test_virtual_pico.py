import json

from host import virtual_pico


def test_virtual_pico_cycles_all_lighting_sequences_with_default_budget(capsys):
    exit_code = virtual_pico.main(
        [
            "--sequence",
            "all",
            "--duration-seconds",
            "0.2",
            "--json",
        ]
    )

    assert exit_code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True
    assert report["sequence"] == "all"
    assert report["pixel_writes"] > 0
    assert report["worst_loop_ms"] <= report["max_loop_budget_ms"]
    assert report["min_free_heap_estimate_bytes"] >= report["min_free_heap_required_bytes"]
    assert report["ota_storage"]["micropython_firmware_bytes"] > 0
    assert report["ota_storage"]["ota_payload_bytes"] > 0
    assert report["ota_storage"]["ota_backup_bytes"] > 0
    assert report["ota_storage"]["bad_build_candidate_deleted_on_rollback"] is True


def test_virtual_pico_reports_budget_failures(capsys):
    exit_code = virtual_pico.main(
        [
            "--sequence",
            "starting-soon",
            "--duration-seconds",
            "0.2",
            "--max-loop-ms",
            "0.1",
            "--json",
        ]
    )

    assert exit_code == 1
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False
    assert any("exceeded budget" in failure for failure in report["failures"])


def test_virtual_pico_reports_ota_storage_failures(capsys):
    exit_code = virtual_pico.main(
        [
            "--sequence",
            "pulse",
            "--duration-seconds",
            "0.1",
            "--flash-bytes",
            "131072",
            "--micropython-bytes",
            "98304",
            "--json",
        ]
    )

    assert exit_code == 1
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False
    assert report["ota_storage"]["filesystem_bytes_estimate"] == 32768
    assert any("storage:" in failure for failure in report["failures"])


def test_virtual_pico_lists_sequences(capsys):
    exit_code = virtual_pico.main(["--list-sequences"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "all\n" in output
    assert "starting-soon\n" in output
