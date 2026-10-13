"""Allocation reservation must cover the actual bounded raw representations."""

import json
from pathlib import Path

import pytest

from btc_microstructure.bounded_live import StateReservations, _Sequencer, _raw_row, _stored_row
from btc_microstructure.offline_storage import _json_line
from btc_microstructure.bounded_writer import _audit_physical_root

from bounded_fakes import FakeClock
from test_bounded_live import bounded_config
from test_unit_binding import resolve_test_binding


@pytest.mark.parametrize(
    "payload",
    [
        b"\xff" * 65_536,
        json.dumps({"e": "aggTrade", "s": "BTCUSDT", "extra": "\\" * 30_000}).encode(),
        json.dumps(
            {"e": "aggTrade", "s": "BTCUSDT", "extra": "\u2603" * 20_000}, ensure_ascii=False
        ).encode(),
    ],
    ids=["invalid-utf8", "escaped-json", "unicode-json"],
)
def test_raw_reservation_covers_pending_and_writer_encoded_bytes(
    tmp_path: Path, payload: bytes
) -> None:
    assert len(payload) <= 65_536
    reservation = StateReservations(bounded_config(tmp_path)).reserve_raw_payload(len(payload))
    row = _raw_row(_Sequencer(), FakeClock(), "TEST_ONLY", payload, resolve_test_binding())
    # Observe actual representations, independently of the reservation formula.
    actual_encoded = len(_json_line(row)) + len(_json_line(_stored_row(row)))
    assert reservation >= actual_encoded


def test_uncommitted_raw_file_is_charged_as_orphan(tmp_path: Path) -> None:
    root = tmp_path / "run"
    (root / "raw").mkdir(parents=True)
    path = root / "raw" / "raw-control-000001.jsonl"
    content = b'{"kind":"RAW","ingest_sequence":1}\n'
    path.write_bytes(content)
    audit = _audit_physical_root(root)
    assert audit.charges["temporary_orphan_quarantine_files"] == len(content)
    assert audit.charges["ws_raw_envelopes"] == 0
    assert path.read_bytes() == content
