from __future__ import annotations

import json
import os
import shutil
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from btc_microstructure import offline_storage
from btc_microstructure.offline_storage import Store, StorageError


def row(seq: int, *, ts: int | None = None, flags: list[str] | None = None) -> dict[str, Any]:
    return {
        "schema_version": "test-raw-1",
        "adapter_version": "test-adapter-1",
        "venue": "binance",
        "market_type": "um_perpetual",
        "instrument_id": "binance:um_perpetual:BTCUSDT",
        "source_stream": "um_agg_trade",
        "source_origin": "archive",
        "evidence_kind": "SYNTHETIC",
        "source_time_unit": "ms",
        "ts_receive_ns": ts if ts is not None else 1_000_000_000 + seq,
        "session_id": "synthetic-session",
        "ingest_sequence": seq,
        "raw_payload": {"a": seq, "p": "100.01", "q": "0.10"},
        "quality_flags": flags or [],
    }


def read_error_events(root: Path) -> list[dict[str, Any]]:
    path = root / "errors" / "storage_events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def break_manifest(root: Path, name: str, updates: dict[str, Any]) -> None:
    path = root / "manifests" / name
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(updates)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_flush_commits_raw_manifest_and_advances_cursor_after_verification(tmp_path):
    store = Store(tmp_path, run_metadata={"run_id": "r1-03-storage-test"})
    store.append(row(1))
    store.append(row(2, flags=["GAP_KNOWN"]))

    manifest = store.flush()

    assert manifest is not None
    assert store.cursor == 2
    assert manifest["kind"] == "raw_control_segment"
    assert manifest["row_count"] == 2
    assert manifest["cursor"] == {"before": None, "after": 2}
    assert manifest["quality_counts"] == {"OK": 1, "GAP_KNOWN": 1}
    assert (tmp_path / manifest["path"]).exists()
    assert Store(tmp_path).read_rows() == [row(1), row(2, flags=["GAP_KNOWN"])]


def test_flush_failure_does_not_advance_cursor_and_records_storage_error(tmp_path, monkeypatch):
    store = Store(tmp_path)
    store.append(row(7))
    original_replace = os.replace

    def fail_manifest_replace(src: str | Path, dst: str | Path) -> None:
        if str(dst).endswith(".json"):
            raise OSError("manifest device error")
        original_replace(src, dst)

    monkeypatch.setattr(offline_storage.os, "replace", fail_manifest_replace)

    with pytest.raises(StorageError):
        store.flush()

    assert store.cursor is None
    events = read_error_events(tmp_path)
    assert events[-1]["event_type"] == "STORAGE_ERROR"
    assert events[-1]["reason"] == "FLUSH_FAILED"

    recovered = Store(tmp_path)
    assert recovered.cursor is None
    assert recovered.recovery_events[-1]["event_type"] == "UNCERTAIN_GAP"
    assert recovered.recovery_events[-1]["last_ingest_sequence"] == 7
    assert list((tmp_path / "quarantine").glob("raw-control-000001.jsonl"))


def test_raw_rename_failure_keeps_cursor_and_queue(tmp_path, monkeypatch):
    store = Store(tmp_path)
    store.append(row(3))
    original_replace = os.replace

    def fail_raw_replace(src: str | Path, dst: str | Path) -> None:
        if str(dst).endswith(".jsonl"):
            raise OSError("rename failed")
        original_replace(src, dst)

    monkeypatch.setattr(offline_storage.os, "replace", fail_raw_replace)

    with pytest.raises(StorageError):
        store.flush()

    assert store.cursor is None
    assert store.queue_stats.records == 1
    assert read_error_events(tmp_path)[-1]["reason"] == "FLUSH_FAILED"


def test_temp_write_failure_keeps_cursor_and_reports_enospc(tmp_path, monkeypatch):
    store = Store(tmp_path)
    store.append(row(4))
    original_open = Path.open

    def fail_tmp_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self.name.endswith(".tmp"):
            raise OSError("ENOSPC")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_tmp_open)

    with pytest.raises(StorageError):
        store.flush()

    assert store.cursor is None
    assert store.queue_stats.records == 1
    assert read_error_events(tmp_path)[-1]["reason"] == "FLUSH_FAILED"


def test_low_space_check_blocks_append_before_claiming_continuity(tmp_path):
    usage = shutil._ntuple_diskusage(total=100, used=95, free=5)
    store = Store(tmp_path, min_free_bytes=10, disk_usage=lambda _path: usage)

    with pytest.raises(StorageError, match="disk free"):
        store.append(row(1))

    assert store.cursor is None
    assert read_error_events(tmp_path)[-1]["reason"] == "DISK_FREE_LIMIT"


def test_table_manifest_failure_does_not_publish_table(tmp_path, monkeypatch):
    store = Store(tmp_path)
    original_replace = os.replace

    def fail_table_manifest(src: str | Path, dst: str | Path) -> None:
        if str(dst).endswith(".json") and "manifest-table" in str(dst):
            raise OSError("manifest write failed")
        original_replace(src, dst)

    monkeypatch.setattr(offline_storage.os, "replace", fail_table_manifest)

    with pytest.raises(StorageError):
        store.write_table("normalized", [{"ingest_sequence": 1, "ts_receive_ns": 1}])

    assert not list((tmp_path / "manifests").glob("manifest-table-*.json"))
    assert read_error_events(tmp_path)[-1]["reason"] == "TABLE_WRITE_FAILED"


def test_recovery_ignores_corrupt_manifest_and_keeps_raw_file(tmp_path):
    store = Store(tmp_path)
    store.append(row(1))
    manifest = store.flush()
    assert manifest is not None

    manifest_path = next((tmp_path / "manifests").glob("manifest-raw-*.json"))
    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_data["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest_data), encoding="utf-8")

    recovered = Store(tmp_path)

    assert recovered.cursor is None
    with pytest.raises(StorageError, match="manual review"):
        recovered.append(row(2))
    assert recovered.read_rows() == []
    assert not (tmp_path / manifest["path"]).exists()
    assert (tmp_path / "quarantine" / Path(str(manifest["path"])).name).exists()
    assert "UNTRUSTED_MANIFEST" in {event["reason"] for event in recovered.recovery_events}
    assert read_error_events(tmp_path)[-1]["event_type"] == "UNCERTAIN_GAP"


def test_recovery_stops_at_first_untrusted_raw_commit(tmp_path):
    store = Store(tmp_path)
    store.append(row(1))
    first = store.flush()
    store.append(row(2))
    second = store.flush()
    assert first is not None and second is not None
    first_manifest = tmp_path / "manifests" / "manifest-raw-000001.json"
    manifest_data = json.loads(first_manifest.read_text(encoding="utf-8"))
    manifest_data["sha256"] = "1" * 64
    first_manifest.write_text(json.dumps(manifest_data), encoding="utf-8")

    recovered = Store(tmp_path)

    assert recovered.cursor is None
    assert recovered.read_rows() == []
    assert {"UNTRUSTED_MANIFEST", "AFTER_UNTRUSTED_COMMIT"} <= {
        event["reason"] for event in recovered.recovery_events
    }
    assert (tmp_path / "quarantine" / Path(str(first["path"])).name).exists()
    assert (tmp_path / "quarantine" / Path(str(second["path"])).name).exists()
    with pytest.raises(StorageError, match="manual review"):
        recovered.append(row(3))


def test_bad_manifest_traversal_and_missing_fields_block_recovery(tmp_path):
    store = Store(tmp_path)
    store.append(row(1))
    manifest = store.flush()
    assert manifest is not None
    break_manifest(tmp_path, "manifest-raw-000001.json", {"path": "../outside.jsonl"})

    recovered = Store(tmp_path)
    assert recovered.cursor is None
    assert "UNTRUSTED_MANIFEST" in {event["reason"] for event in recovered.recovery_events}

    other = tmp_path / "missing"
    other_store = Store(other)
    other_store.append(row(1))
    other_store.flush()
    break_manifest(other, "manifest-raw-000001.json", {"row_count": None})

    other_recovered = Store(other)
    assert other_recovered.cursor is None
    assert "UNTRUSTED_MANIFEST" in {event["reason"] for event in other_recovered.recovery_events}


def test_unmanifested_tail_is_quarantined_without_deleting_committed_raw(tmp_path):
    store = Store(tmp_path)
    store.append(row(1))
    manifest = store.flush()
    assert manifest is not None
    tail = tmp_path / "raw" / "raw-control-999999.jsonl"
    tail.write_bytes(offline_storage._json_line(row(9)))  # noqa: SLF001 - tests committed JSONL tail format

    recovered = Store(tmp_path)

    assert recovered.cursor == 1
    assert recovered.read_rows() == [row(1)]
    assert (tmp_path / manifest["path"]).exists()
    assert not tail.exists()
    assert (tmp_path / "quarantine" / tail.name).exists()
    assert recovered.recovery_events[-1]["first_ingest_sequence"] == 9
    assert recovered.recovery_events[-1]["last_ingest_sequence"] == 9


def test_queue_limits_are_observable_and_never_overwrite(tmp_path):
    store = Store(tmp_path, max_records=1, max_bytes=10_000, max_age_ns=10)
    store.append(row(1, ts=100))
    stats = store.queue_stats_at(100)
    assert stats.records == 1
    assert stats.bytes > 0
    assert stats.oldest_age_ns == 0

    with pytest.raises(StorageError, match="record limit"):
        store.append(row(2, ts=105))
    assert [item["ingest_sequence"] for item in store._queue] == [1]  # noqa: SLF001

    age_store = Store(tmp_path / "age", max_records=3, max_bytes=10_000, max_age_ns=10)
    age_store.append(row(1, ts=100))
    with pytest.raises(StorageError, match="age limit"):
        age_store.append(row(2, ts=111))
    assert read_error_events(tmp_path / "age")[-1]["reason"] == "QUEUE_AGE_LIMIT"

    assert store.queue_stats_at(250).oldest_age_ns == 150

    sequence_store = Store(tmp_path / "sequence")
    sequence_store.append(row(1))
    with pytest.raises(StorageError, match="strictly increasing"):
        sequence_store.append(row(1, ts=101))


def test_parquet_tables_use_zstd_decimal_schema_and_union_keys(tmp_path):
    store = Store(tmp_path)
    rows = [
        {
            "instrument_id": "binance:um_perpetual:BTCUSDT",
            "ts_receive_ns": 1,
            "ingest_sequence": 1,
            "price_quote": Decimal("123.45"),
            "quality_flags": [],
        },
        {
            "instrument_id": "binance:um_perpetual:BTCUSDT",
            "ts_receive_ns": 2,
            "ingest_sequence": 2,
            "price_quote": Decimal("0.000000000000000001"),
            "late_column": "present-only-in-second-row",
            "quality_flags": ["UNKNOWN_UNIT"],
        },
    ]

    manifest = store.write_table("normalized", rows)
    readback = store.read_table("normalized")

    assert manifest["kind"] == "derived_table"
    assert manifest["row_count"] == 2
    assert manifest["quality_counts"] == {"OK": 1, "UNKNOWN_UNIT": 1}
    assert "late_column" in manifest["versions"]["arrow_schema"]
    assert "decimal128(38, 18)" in manifest["versions"]["arrow_schema"]
    assert readback[0]["price_quote"] == Decimal("123.450000000000000000")
    assert readback[0]["late_column"] is None
    assert readback[0]["quality_flags"] == []
    assert readback[1]["late_column"] == "present-only-in-second-row"
    assert readback[1]["quality_flags"] == ["UNKNOWN_UNIT"]


def test_mutating_appended_row_does_not_change_committed_raw(tmp_path):
    store = Store(tmp_path)
    mutable = row(1)
    store.append(mutable)
    mutable["raw_payload"]["q"] = "99.99"
    store.flush()

    assert store.read_rows()[0]["raw_payload"]["q"] == "0.10"


def test_close_flushes_and_rejects_late_appends(tmp_path):
    store = Store(tmp_path)
    store.append(row(1))
    store.close()

    assert Store(tmp_path).cursor == 1
    with pytest.raises(StorageError, match="closed"):
        store.append(row(2))


def test_recovers_manifest_temporary_tail_without_losing_raw(tmp_path):
    store = Store(tmp_path)
    store.append(row(1))
    manifest = store.flush()
    temporary = tmp_path / "manifests" / "manifest-raw-000002.json.tmp"
    temporary.write_text('{"partial":')
    recovered = Store(tmp_path)
    assert recovered.cursor == 1
    assert recovered.read_rows() == [row(1)]
    assert not temporary.exists()
    assert (tmp_path / "quarantine" / temporary.name).read_text() == '{"partial":'
    assert recovered.recovery_events[0]["event_type"] == "UNCERTAIN_GAP"
    assert (tmp_path / manifest["path"]).exists()


def test_tail_uncertainty_survives_second_reopen(tmp_path):
    original = Store(tmp_path)
    original.append(row(1))
    original.flush()
    (tmp_path / "raw" / "raw-control-000002.jsonl.tmp").write_text("incomplete")
    first = Store(tmp_path)
    second = Store(tmp_path)
    assert first.cursor == second.cursor == 1
    assert first.recovery_events and second.recovery_events
    assert second.recovery_events[0]["event_type"] == "UNCERTAIN_GAP"


def test_commit_error_blocks_in_process_retries_even_after_io_recovers(tmp_path, monkeypatch):
    store = Store(tmp_path)
    store.append(row(1))
    original = offline_storage._write_json_atomic

    def fail(*args):
        raise OSError("manifest ambiguous failure")

    monkeypatch.setattr(offline_storage, "_write_json_atomic", fail)
    with pytest.raises(StorageError):
        store.flush()
    monkeypatch.setattr(offline_storage, "_write_json_atomic", original)
    with pytest.raises(StorageError, match="previous storage commit failed"):
        store.flush()
    with pytest.raises(StorageError, match="previous storage commit failed"):
        store.append(row(2))
    assert store.cursor is None
    assert store.queue_stats.records == 1


def test_missing_derived_file_does_not_invent_raw_uncertainty(tmp_path):
    original = Store(tmp_path)
    original.append(row(1))
    original.flush()
    table = original.write_table("features", [{"feature_cutoff_ns": 10}])
    (tmp_path / table["path"]).unlink()
    recovered = Store(tmp_path)
    assert recovered.cursor == 1
    assert recovered.read_rows() == [row(1)]
    assert recovered.recovery_events
    assert all(event["event_type"] == "DERIVED_INVALID" for event in recovered.recovery_events)
