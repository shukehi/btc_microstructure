"""L06: compare against original tables and retain publication uncertainty."""

from decimal import Decimal
from pathlib import Path

from btc_microstructure.bounded_replay import replay_bounded
from btc_microstructure.bounded_transport import WsEvent
from btc_microstructure.offline_core import NS
from btc_microstructure.offline_storage import Store

from bounded_fakes import FakeClock, FakeTransport, agg_trade
from test_bounded_live import bounded_config, run_controller, tree_digest


def fixture_run(tmp_path: Path) -> Path:
    clock = FakeClock()
    start = clock.wall_ns()
    transport = FakeTransport(
        clock,
        [
            (
                start + 100_000_000,
                WsEvent("message", agg_trade(agg_id=1, quantity="0.30", buyer_is_maker=False)),
            ),
            (
                start + 200_000_000,
                WsEvent("message", agg_trade(agg_id=2, quantity="0.20", buyer_is_maker=False)),
            ),
            (
                start + 300_000_000,
                WsEvent("message", agg_trade(agg_id=3, quantity="0.10", buyer_is_maker=True)),
            ),
            (
                start + NS,
                WsEvent("message", agg_trade(agg_id=2, quantity="0.20", buyer_is_maker=False)),
            ),
            (start + 2 * NS, WsEvent("disconnect")),
        ],
    )
    config = bounded_config(tmp_path, window_seconds=[1])
    run_controller(config, transport, clock)
    return config.output_root


def test_two_replays_match_original_and_independent_oracle(tmp_path: Path) -> None:
    root = fixture_run(tmp_path)
    before = tree_digest(root)
    first = replay_bounded(root)
    second = replay_bounded(root)
    assert first == second
    assert first["status"] == "PASS"
    assert first["replay_matches_original"] is True
    assert first["original_logical_sha256"] == first["replay_logical_sha256"]
    feature = first["features"][0]
    assert feature["buy_volume_base"] == Decimal("0.50")
    assert feature["sell_volume_base"] == Decimal("0.10")
    assert feature["volume_base"] == Decimal("0.60")
    assert feature["trade_delta_base"] == Decimal("0.40")
    assert feature["trade_imbalance"] == 2 / 3
    assert feature["quality_flags"] == []
    assert feature["feature_available_ns"] >= feature["feature_cutoff_ns"]
    assert tree_digest(root) == before


def test_valid_but_incorrect_derived_table_is_not_a_replay_pass(tmp_path: Path) -> None:
    root = fixture_run(tmp_path)
    # Only this newly generated test fixture is edited, never archived evidence.
    store = Store(root)
    values = store.read_table("features")
    values[0]["trade_delta_base"] = Decimal("9.99")
    store.write_table("features", values)
    before = tree_digest(root)
    result = replay_bounded(root)
    assert result["status"] == "FAIL"
    assert result["replay_matches_original"] is False
    assert result["original_logical_sha256"] != result["replay_logical_sha256"]
    assert tree_digest(root) == before


def test_uncommitted_tail_is_uncertain_and_never_repaired(tmp_path: Path) -> None:
    root = fixture_run(tmp_path)
    (root / "raw" / "orphan.jsonl.tmp").write_bytes(b"partial raw bytes")
    before = tree_digest(root)
    result = replay_bounded(root)
    assert result["status"] == "PARTIAL"
    assert result["recovery_events"]
    assert result["recovery_events"][0]["event_type"] == "UNCERTAIN_GAP"
    assert tree_digest(root) == before


def test_missing_published_receipt_keeps_available_null(tmp_path: Path) -> None:
    source = fixture_run(tmp_path / "source")
    original = Store(source)
    prefix = []
    for row in original.read_rows():
        prefix.append(row)
        if row["kind"] == "FEATURE_CUTOFF":
            break
    root = tmp_path / "without-receipt"
    store = Store(root)
    (root / "snapshots").mkdir()
    (root / "snapshots" / "run-context.json").write_bytes(
        (source / "snapshots" / "run-context.json").read_bytes()
    )
    for row in prefix:
        store.append(row)
    store.flush()
    before = tree_digest(root)
    result = replay_bounded(root)
    assert result["status"] == "PARTIAL"
    assert result["features"][0]["feature_available_ns"] is None
    assert result["features"][0]["publication_status"] == "UNVERIFIED"
    assert result["features"][0]["trade_delta_base"] == Decimal("0.40")
    assert result["original_logical_sha256"] is None
    assert result["replay_matches_original"] is None
    assert tree_digest(root) == before
