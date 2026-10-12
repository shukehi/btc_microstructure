"""Finite R1-04 control-failure checks, independent of default test collection.

Oracle: docs/02 sections 6/8 and docs/03 section1 forbid healthy zero on
severe control faults. No transport, real clock change, or physical disk fault.
"""

import socket

import pytest

from btc_microstructure.offline_core import Core, NS


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("R1-04 followup forbids network")

    for name in ("create_connection", "getaddrinfo"):
        monkeypatch.setattr(socket, name, forbidden)
    for name in ("connect", "connect_ex"):
        monkeypatch.setattr(socket.socket, name, forbidden)


@pytest.mark.parametrize("control", ["CLOCK_UNCERTAIN", "STORAGE_ERROR"])
def test_control_fault_is_not_published_as_healthy_zero(control):
    core = Core({"time_view": "as_received", "window_seconds": [30]}, {})
    core.process(
        {
            "kind": "SESSION_OPEN",
            "session_id": "synthetic",
            "ingest_sequence": 1,
            "ts_receive_ns": 0,
        }
    )
    core.process({"kind": control, "ingest_sequence": 2, "ts_receive_ns": 30 * NS})
    core.process(
        {
            "kind": "FEATURE_CUTOFF",
            "ingest_sequence": 3,
            "ts_receive_ns": 31 * NS,
            "cutoff_ns": 31 * NS,
            "input_cursor": 2,
        }
    )
    assert core.features == []
    assert core.pending_features[0]["feature_available_ns"] is None
    historical_publish = 32 * NS
    core.process(
        {
            "kind": "FEATURE_PUBLISHED",
            "ingest_sequence": 4,
            "ts_receive_ns": historical_publish,
            "cutoff_ns": 31 * NS,
            "feature_available_ns": historical_publish,
        }
    )
    feature = core.features[0]
    assert feature["feature_available_ns"] == historical_publish
    assert "FATAL_HEALTH" in feature["quality_flags"]
    assert feature["data_valid"] is False
    for column in (
        "buy_volume_base",
        "sell_volume_base",
        "volume_base",
        "trade_delta_base",
        "trade_imbalance",
        "agg_message_count",
    ):
        assert feature[column] is None


def test_receive_clock_regression_stops_without_inventing_publication():
    core = Core({"time_view": "as_received", "window_seconds": [30]}, {})
    core.process(
        {
            "kind": "SESSION_OPEN",
            "session_id": "synthetic",
            "ingest_sequence": 1,
            "ts_receive_ns": 5 * NS,
        }
    )
    with pytest.raises(ValueError, match="CLOCK_UNCERTAIN"):
        core.process({"kind": "CLOCK_UNCERTAIN", "ingest_sequence": 2, "ts_receive_ns": 4 * NS})
    assert core.fatal is True
    assert core.features == []
    assert core.cursor == 1
