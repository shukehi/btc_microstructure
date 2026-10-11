"""R103-A/B regressions, including real Parquet rebuild from a failed raw tail."""

from decimal import Decimal
import shutil

import pytest
import yaml

from btc_microstructure import offline_storage as storage
from btc_microstructure.offline import OfflineConfig, build_features, run_synthetic
from btc_microstructure.offline_storage import Store, StorageError
from conftest import ROOT


def raw(sequence):
    return {"kind": "RAW", "ingest_sequence": sequence, "ts_receive_ns": sequence, "payload": "{}"}


def fail_io(monkeypatch, stage):
    def failed(*args, **kwargs):
        raise OSError("SYNTHETIC injected raw commit failure")

    if stage == "fsync":
        monkeypatch.setattr(storage.os, "fsync", failed)
    elif stage == "rename":
        monkeypatch.setattr(storage.os, "replace", failed)
    else:
        monkeypatch.setattr(storage, "_write_json_atomic", failed)


@pytest.mark.parametrize("stage", ["fsync", "rename", "manifest"])
def test_failed_raw_commit_keeps_bytes_cursor_and_uncertainty_across_reopens(
    tmp_path, monkeypatch, stage
):
    first = Store(tmp_path)
    first.append(raw(1))
    committed = first.flush()
    committed_bytes = (tmp_path / committed["path"]).read_bytes()
    first.append(raw(2))
    with monkeypatch.context() as patch:
        fail_io(patch, stage)
        with pytest.raises(StorageError):
            first.flush()
    old_audit = first.error_log.read_bytes()
    assert first.cursor == 1
    for _ in range(2):
        recovered = Store(tmp_path)
        assert recovered.cursor == 1
        assert recovered.read_rows() == [raw(1)]
        assert any(e["event_type"] == "UNCERTAIN_GAP" for e in recovered.recovery_events)
        assert recovered.error_log.read_bytes().startswith(old_audit)
        assert (tmp_path / committed["path"]).read_bytes() == committed_bytes
    expected = b'{"ingest_sequence":2,"kind":"RAW","payload":"{}","ts_receive_ns":2}\n'
    assert any(p.read_bytes() == expected for p in (tmp_path / "quarantine").iterdir())
    failure = next(e for e in recovered.recovery_events if e["reason"] == "RAW_COMMIT_FAILED")
    assert failure["trusted_cursor"] == 1
    assert failure["first_ingest_sequence"] == failure["last_ingest_sequence"] == 2


def test_quarantined_raw_is_recovery_evidence_when_failure_audit_cannot_be_written(
    tmp_path, monkeypatch
):
    first = Store(tmp_path)
    first.append(raw(1))
    first.flush()
    first.append(raw(2))
    with monkeypatch.context() as patch:
        fail_io(patch, "fsync")
        patch.setattr(Store, "_record_error", lambda *args: None)
        with pytest.raises(StorageError):
            first.flush()
    assert not first.error_log.exists()
    for _ in range(2):
        recovered = Store(tmp_path)
        assert recovered.cursor == 1
        assert recovered.read_rows() == [raw(1)]
        assert any(e["reason"] == "QUARANTINED_RAW_TAIL" for e in recovered.recovery_events)
    assert any(
        p.read_bytes().endswith(b'"ts_receive_ns":2}\n')
        for p in (tmp_path / "quarantine").iterdir()
    )


def test_legacy_raw_failure_audit_is_not_rewritten_or_forgotten(tmp_path):
    first = Store(tmp_path)
    first.append(raw(1))
    first.flush()
    original_audit = b'{"event_type":"STORAGE_ERROR","reason":"FLUSH_FAILED","ingest_sequence":1}\n'
    first.error_log.write_bytes(original_audit)
    recovered = Store(tmp_path)
    assert recovered.cursor == 1
    assert recovered.error_log.read_bytes() == original_audit
    assert recovered.recovery_events[0]["event_type"] == "UNCERTAIN_GAP"
    assert recovered.recovery_events[0]["reason"] == "RAW_COMMIT_FAILED"


@pytest.mark.parametrize("tail", [b"\xff", b"7\n", b"[]\n", b'\xff\n{"ingest_sequence":9}\n['])
def test_untrusted_tail_diagnostics_are_best_effort_and_preserve_every_byte(tmp_path, tail):
    first = Store(tmp_path)
    first.append(raw(1))
    first.flush()
    bad = tmp_path / "raw" / "raw-control-000002.jsonl.tmp"
    bad.write_bytes(tail)
    for _ in range(2):
        recovered = Store(tmp_path)
        assert recovered.cursor == 1
        assert recovered.read_rows() == [raw(1)]
        assert any(e["event_type"] == "UNCERTAIN_GAP" for e in recovered.recovery_events)
    assert not bad.exists()
    assert any(p.read_bytes() == tail for p in (tmp_path / "quarantine").iterdir())
    event = next(e for e in recovered.recovery_events if e.get("path", "").startswith("raw/"))
    expected_sequence = 9 if b"ingest_sequence" in tail else None
    assert event["first_ingest_sequence"] == event["last_ingest_sequence"] == expected_sequence
    assert event["diagnostic_errors"]


def test_failed_raw_tail_propagates_through_repeated_raw_only_parquet_builds(tmp_path, monkeypatch):
    config_values = yaml.safe_load((ROOT / "config/offline.synthetic.yaml").read_text())
    config_values["synthetic_input"] = str(
        ROOT / "fixtures/synthetic/vertical_slice/warmed_delta.json"
    )
    config_values["limits"]["min_free_bytes"] = 0
    config_path = tmp_path / "offline.yaml"
    config_path.write_text(yaml.safe_dump(config_values))
    config = OfflineConfig.load(config_path)
    complete = tmp_path / "complete"
    assert run_synthetic(config, complete)["status"] == "PASS"
    source_root = tmp_path / "raw-only"
    shutil.copytree(complete / "raw", source_root / "raw")
    (source_root / "manifests").mkdir()
    for manifest in (complete / "manifests").glob("manifest-raw-*.json"):
        shutil.copy2(manifest, source_root / "manifests" / manifest.name)
    source = Store(source_root)
    trusted_cursor = source.cursor
    pending = raw(trusted_cursor + 1)
    pending["ts_receive_ns"] = 36_000_000_000
    source.append(pending)
    with monkeypatch.context() as patch:
        fail_io(patch, "fsync")
        with pytest.raises(StorageError):
            source.flush()
    for attempt in range(2):
        destination = tmp_path / f"rebuilt-{attempt}"
        summary = build_features(config, source_root, destination)
        assert summary["status"] == "FAIL"
        assert any(e["event_type"] == "UNCERTAIN_GAP" for e in summary["recovery_events"])
        assert Store(source_root).cursor == trusted_cursor
        # The trusted prefix remains recomputable; it cannot claim raw completeness.
        features = Store(destination).read_table("features")
        fixed = next(
            r
            for r in features
            if r["feature_cutoff_ns"] == 31_000_000_000 and r["window_seconds"] == 30
        )
        assert fixed["volume_base"] == Decimal("0.60")
        assert fixed["trade_delta_base"] == Decimal("0.40")
    assert not list((source_root / "tables").glob("*/*.parquet"))


def test_pure_derived_write_failure_does_not_create_raw_gap(tmp_path, monkeypatch):
    values = yaml.safe_load((ROOT / "config/offline.synthetic.yaml").read_text())
    values["synthetic_input"] = str(ROOT / "fixtures/synthetic/vertical_slice/warmed_delta.json")
    values["limits"]["min_free_bytes"] = 0
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(values))
    config = OfflineConfig.load(config_path)
    source_root = tmp_path / "complete"
    assert run_synthetic(config, source_root)["status"] == "PASS"
    source = Store(source_root)
    with monkeypatch.context() as patch:
        fail_io(patch, "fsync")
        with pytest.raises(StorageError):
            source.write_table("additional_derived", [{"value": 1}])
    assert list((source_root / "quarantine").glob("*.parquet.tmp"))
    recovered = Store(source_root)
    assert not any(event["event_type"] == "UNCERTAIN_GAP" for event in recovered.recovery_events)
    summary = build_features(config, source_root, tmp_path / "rebuilt")
    assert summary["status"] == "PASS"
