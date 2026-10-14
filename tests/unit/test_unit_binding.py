from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from btc_microstructure.unit_binding import (
    TEST_ONLY_APPROVAL_KIND,
    STREAM,
    UNIT_EVIDENCE_ID,
    UnitBinding,
    UnitBindingError,
    load_unit_evidence,
    resolve_live_unit_binding,
    resolve_test_only_unit_binding,
    sha256_bytes,
)


EVIDENCE_PATH = Path("reports/round2/units_and_collection_plan/quantity_unit_evidence.json")


def evidence_fixture() -> tuple[dict[str, object], str]:
    return load_unit_evidence(EVIDENCE_PATH.read_bytes())


def metadata_fixture(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "symbol": "BTCUSDT",
        "baseAsset": "BTC",
        "quoteAsset": "USDT",
        "marginAsset": "USDT",
        "contractType": "PERPETUAL",
        "status": "TRADING",
        "tickSize": "0.10",
        "stepSize": "0.001",
        "minQty": "0.001",
    }
    value.update(updates)
    return value


def approval_fixture(
    evidence_sha256: str,
    *,
    kind: str = TEST_ONLY_APPROVAL_KIND,
    filters: dict[str, str] | None = None,
    **updates: object,
) -> dict[str, object]:
    value: dict[str, object] = {
        "kind": kind,
        "approval_id": "TEST_ONLY:r2-04:unit-binding",
        "unit_evidence_id": UNIT_EVIDENCE_ID,
        "evidence_version": "1",
        "evidence_sha256": evidence_sha256,
        "instrument_id": "binance:um_perpetual:BTCUSDT",
        "source_stream": "um_agg_trade",
        "stream_name": STREAM,
        "st_if_present": 1,
        "filters": filters or {"tickSize": "0.10", "stepSize": "0.001", "minQty": "0.001"},
    }
    value.update(updates)
    return value


def resolve_test_binding(
    *,
    metadata: dict[str, object] | None = None,
    approval: dict[str, object] | None = None,
    source_stream: str = STREAM,
    symbol_type_raw: int | None = 1,
) -> UnitBinding:
    evidence, evidence_sha = evidence_fixture()
    return resolve_test_only_unit_binding(
        evidence=evidence,
        evidence_sha256=evidence_sha,
        current_metadata=metadata or metadata_fixture(),
        approval=approval or approval_fixture(evidence_sha),
        source_stream=source_stream,
        symbol_type_raw=symbol_type_raw,
    )


def test_candidate_loads_with_exact_file_hash_and_remains_pending() -> None:
    raw = EVIDENCE_PATH.read_bytes()
    evidence, evidence_sha = load_unit_evidence(raw)

    assert evidence_sha == sha256_bytes(raw)
    assert evidence["unit_evidence_id"] == UNIT_EVIDENCE_ID
    assert evidence["review_status"] == "PENDING_INDEPENDENT_REVIEW"
    assert evidence["runtime_applied"] is False


def test_live_binding_fails_closed_for_pending_evidence_and_unknown_shared_budget() -> None:
    evidence, evidence_sha = evidence_fixture()

    with pytest.raises(UnitBindingError, match="shared_budget"):
        resolve_live_unit_binding(
            evidence=evidence,
            evidence_sha256=evidence_sha,
            current_metadata=metadata_fixture(),
            approval=None,
            shared_budget_trusted=False,
            source_stream=STREAM,
            symbol_type_raw=1,
        )

    with pytest.raises(UnitBindingError, match="formal live approval"):
        resolve_live_unit_binding(
            evidence=evidence,
            evidence_sha256=evidence_sha,
            current_metadata=metadata_fixture(),
            approval=None,
            shared_budget_trusted=True,
            source_stream=STREAM,
            symbol_type_raw=1,
        )

    with pytest.raises(UnitBindingError, match="review_status APPROVED"):
        resolve_live_unit_binding(
            evidence=evidence,
            evidence_sha256=evidence_sha,
            current_metadata=metadata_fixture(),
            approval=approval_fixture(evidence_sha, kind="FORMAL_LIVE_UNIT_BINDING_APPROVAL"),
            shared_budget_trusted=True,
            source_stream=STREAM,
            symbol_type_raw=1,
        )


def test_test_only_approval_resolves_immutable_btc_binding_for_exact_identity() -> None:
    binding = resolve_test_binding()

    assert binding.runtime_scope == "TEST_ONLY_OFFLINE"
    assert binding.quantity_unit == "BTC"
    assert binding.normal_quantity_unit == "BTC"
    assert binding.price_unit == "USDT/BTC"
    assert binding.notional_unit == "USDT"
    assert binding.quantity_multiplier_decimal == "1"
    assert binding.price_tick == "0.10"
    assert binding.quantity_step == "0.001"
    assert binding.minimum_quantity == "0.001"
    assert binding.core_metadata()["unit_evidence_id"] == UNIT_EVIDENCE_ID
    with pytest.raises(FrozenInstanceError):
        binding.quantity_unit = "UNKNOWN"  # type: ignore[assignment, misc]


def test_test_only_approval_rejects_hash_version_and_id_mismatches() -> None:
    evidence, evidence_sha = evidence_fixture()

    bad_hash = approval_fixture("0" * 64)
    with pytest.raises(UnitBindingError, match="approval.evidence_sha256"):
        resolve_test_only_unit_binding(
            evidence=evidence,
            evidence_sha256=evidence_sha,
            current_metadata=metadata_fixture(),
            approval=bad_hash,
            source_stream=STREAM,
            symbol_type_raw=1,
        )

    bad_id = approval_fixture(evidence_sha, unit_evidence_id="DOC:other")
    with pytest.raises(UnitBindingError, match="approval.unit_evidence_id"):
        resolve_test_only_unit_binding(
            evidence=evidence,
            evidence_sha256=evidence_sha,
            current_metadata=metadata_fixture(),
            approval=bad_id,
            source_stream=STREAM,
            symbol_type_raw=1,
        )

    bad_version = approval_fixture(evidence_sha, evidence_version="2")
    with pytest.raises(UnitBindingError, match="approval.evidence_version"):
        resolve_test_only_unit_binding(
            evidence=evidence,
            evidence_sha256=evidence_sha,
            current_metadata=metadata_fixture(),
            approval=bad_version,
            source_stream=STREAM,
            symbol_type_raw=1,
        )


def test_mutated_loaded_evidence_cannot_reuse_old_trusted_hash() -> None:
    evidence, evidence_sha = evidence_fixture()
    evidence["review_status"] = "APPROVED"

    with pytest.raises(UnitBindingError, match="__trusted_canonical_sha256__"):
        resolve_test_only_unit_binding(
            evidence=evidence,
            evidence_sha256=evidence_sha,
            current_metadata=metadata_fixture(),
            approval=approval_fixture(evidence_sha),
            source_stream=STREAM,
            symbol_type_raw=1,
        )


def test_current_metadata_identity_and_filters_are_gated() -> None:
    with pytest.raises(UnitBindingError, match="metadata.contractType"):
        resolve_test_binding(metadata=metadata_fixture(contractType="CURRENT_QUARTER"))

    with pytest.raises(UnitBindingError, match="metadata.quoteAsset"):
        resolve_test_binding(metadata=metadata_fixture(quoteAsset="USD"))

    evidence, evidence_sha = evidence_fixture()
    approval = approval_fixture(
        evidence_sha, filters={"tickSize": "0.01", "stepSize": "0.001", "minQty": "0.001"}
    )
    with pytest.raises(UnitBindingError, match="metadata.price_tick"):
        resolve_test_only_unit_binding(
            evidence=evidence,
            evidence_sha256=evidence_sha,
            current_metadata=metadata_fixture(),
            approval=approval,
            source_stream=STREAM,
            symbol_type_raw=1,
        )


def test_metadata_duplicate_filters_and_bool_st_are_rejected() -> None:
    metadata: dict[str, object] = {
        "symbol": "BTCUSDT",
        "baseAsset": "BTC",
        "quoteAsset": "USDT",
        "marginAsset": "USDT",
        "contractType": "PERPETUAL",
        "status": "TRADING",
        "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
            {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
            {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
        ],
    }
    with pytest.raises(UnitBindingError, match="duplicate filter"):
        resolve_test_binding(metadata=metadata)

    evidence, evidence_sha = evidence_fixture()
    approval = approval_fixture(evidence_sha, st_if_present=True)
    with pytest.raises(UnitBindingError, match="approval.st_if_present"):
        resolve_test_only_unit_binding(
            evidence=evidence,
            evidence_sha256=evidence_sha,
            current_metadata=metadata_fixture(),
            approval=approval,
            source_stream=STREAM,
            symbol_type_raw=1,
        )


def test_source_stream_and_symbol_type_are_gated() -> None:
    with pytest.raises(UnitBindingError, match="source_stream"):
        resolve_test_binding(source_stream="btcusdt@trade")

    with pytest.raises(UnitBindingError, match="symbol_type_raw"):
        resolve_test_binding(symbol_type_raw=2)

    binding = resolve_test_binding(symbol_type_raw=None)
    assert binding.st_if_present == 1
