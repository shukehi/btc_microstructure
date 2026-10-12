"""Focused R104-01 contract checks; pure kernel only, no storage/network.

Run with the project's normal package to verify the same behavior in its lock.
The reviewer executed these against the unchanged Core blob and source-extracted
parser helpers. These cases do not exercise Parquet or CLI.
"""

from decimal import Decimal
import json
import socket

import pytest
from btc_microstructure.offline_core import Core, NS


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network forbidden in R104-01 review")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)


def run_case(*, flagged=False, controls=False):
    metadata = {
        "quantity_unit": "BTC",
        "evidence_kind": "SYNTHETIC",
        "unit_evidence_id": "SYNTHETIC:r104-review",
        "metadata_version": "r104-review",
        "price_tick": "0.1",
        "quantity_step": "0.01",
        "minimum_quantity": "0.01",
    }
    engine = Core({"time_view": "as_received", "window_seconds": [30]}, metadata)

    def add(kind, receive, **extra):
        engine.process(
            {
                "kind": kind,
                "session_id": "s",
                "ingest_sequence": engine.cursor + 1,
                "ts_receive_ns": receive,
                **extra,
            }
        )

    add("SESSION_OPEN", 0)
    for aid, seconds, qty, maker in (
        (1, 2, "0.30", False),
        (2, 3, "0.20", False),
        (3, 4, "0.10", True),
        (2, 5, "0.20", False),
    ):
        trade_time = {1: 2000, 2: 3000, 3: 4000}[aid]
        payload = {
            "e": "aggTrade",
            "s": "BTCUSDT",
            "a": aid,
            "f": aid,
            "l": aid,
            "E": seconds * 1000,
            "T": trade_time,
            "p": "100.0",
            "q": qty,
            "m": maker,
        }
        add(
            "RAW",
            seconds * NS,
            payload=json.dumps(payload),
            raw_ref=f"raw:{seconds}",
            evidence_kind="SYNTHETIC",
            source_time_unit="ms",
            quality_flags=["GAP_KNOWN"] if flagged and aid == 1 else [],
        )
    if controls:
        add("GAP_OPEN", 6 * NS)
        add("GAP_CLOSE", 7 * NS)
    high = engine.cursor
    add("FEATURE_CUTOFF", 31 * NS, cutoff_ns=31 * NS, input_cursor=high)
    add("FEATURE_PUBLISHED", 31 * NS + 1, cutoff_ns=31 * NS, feature_available_ns=31 * NS + 1)
    return engine


def test_healthy_fixed_control():
    core = run_case()
    feature = core.features[0]
    assert feature["volume_base"] == Decimal("0.60")
    assert feature["trade_delta_base"] == Decimal("0.40")
    assert feature["agg_message_count"] == 3
    assert feature["quality_flags"] == []


def test_gap_control_events_still_invalidate():
    core = run_case(controls=True)
    assert core.features[0]["trade_delta_base"] is None
    assert "GAP_KNOWN" in core.features[0]["quality_flags"]


def test_raw_gap_survives_normalization():
    core = run_case(flagged=True)
    raw = next(r for r in core.history if r["kind"] == "RAW")
    assert raw["quality_flags"] == ["GAP_KNOWN"]
    assert "GAP_KNOWN" in core.normalized[0]["quality_flags"]


def test_raw_gap_invalidates_flow_window():
    core = run_case(flagged=True)
    feature = core.features[0]
    assert feature["trade_delta_base"] is None, feature
    assert feature["volume_base"] is None
    assert "GAP_KNOWN" in feature["quality_flags"]
