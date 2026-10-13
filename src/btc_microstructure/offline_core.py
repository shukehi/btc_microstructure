"""R1-03 synthetic, as-received kernel; no transport or wall clock access."""

from copy import deepcopy
from decimal import Decimal, localcontext
import json
from typing import Any

from .probe import ProbeError, decimal_string, integer, object_json
from .unit_binding import UnitBinding

SCHEMA_VERSION = "r1-03.1"
ADAPTER_VERSION = "um-agg-synthetic.1"
NORMALIZATION_VERSION = "trades.2-quality"
FEATURE_VERSION = "delta.2-quality"
BOUND_ADAPTER_VERSION = "um-agg-test-only-live.1"
BOUND_NORMALIZATION_VERSION = "trades.2-quality-test-only-live"
BOUND_FEATURE_VERSION = "delta.2-quality-test-only-live"
NS = 1_000_000_000
TARGET_VENUE = "binance"
TARGET_MARKET_TYPE = "um_perpetual"
TARGET_SYMBOL = "BTCUSDT"
TARGET_INSTRUMENT_ID = "binance:um_perpetual:BTCUSDT"
TARGET_SOURCE_STREAM = "um_agg_trade"
TARGET_WS_STREAM = "btcusdt@aggTrade"

# docs/01 section12. Book/BBO-specific flags and SOURCE_SAMPLED remain
# traceable without invalidating the trade family. CLOCK_UNCERTAIN on a RAW
# observation retains its documented lead-lag restriction; the existing clock
# control event still causes fatal health degradation.
QUALITY_FLAGS = frozenset(
    {
        "WARMUP",
        "GAP_KNOWN",
        "SEQUENCE_INVALID",
        "COVERAGE_INSUFFICIENT",
        "STALE",
        "CLOCK_UNCERTAIN",
        "UNIT_UNKNOWN",
        "SOURCE_SAMPLED",
        "DUPLICATE_CONFLICT",
        "SOURCE_SCHEMA_CHANGED",
        "HISTORY_ONLY",
    }
)
FLOW_INVALID_FLAGS = frozenset(
    {
        "WARMUP",
        "GAP_KNOWN",
        "UNIT_UNKNOWN",
        "DUPLICATE_CONFLICT",
        "SOURCE_SCHEMA_CHANGED",
        "HISTORY_ONLY",
    }
)


def incoming_quality(row: dict[str, Any]) -> list[str]:
    # Older artificial envelopes omitted the field. Explicit null is not absence.
    flags = row.get("quality_flags", [])
    if not isinstance(flags, list) or any(
        not isinstance(flag, str) or flag not in QUALITY_FLAGS for flag in flags
    ):
        raise ProbeError("quality_flags: expected list of documented quality strings")
    return sorted(set(flags))


def exact(value: Any, field: str, zero: bool = False) -> Decimal:
    number = Decimal(decimal_string(value, field, allow_zero=zero))
    # Decimal128 (38,18), shared exact on-disk representation. Never round silently.
    if number.adjusted() >= 20 or (
        isinstance(number.as_tuple().exponent, int) and int(number.as_tuple().exponent) < -18
    ):
        raise ProbeError(f"{field}: outside exact Decimal128(38,18) range")
    return number


def _unit_policy(
    config: dict[str, Any], metadata: dict[str, Any], binding: UnitBinding | None
) -> dict[str, Any]:
    if "unit_binding" in config:
        raise ValueError("unit_binding: pass an explicit binding=UnitBinding object to Core")
    if binding is None:
        return {
            "mode": "SYNTHETIC_DEFAULT",
            "accepted_raw_evidence_kind": "SYNTHETIC",
            "metadata_evidence_kind": "SYNTHETIC",
            "unit_evidence_id": metadata.get("unit_evidence_id"),
            "dataset_revision": "synthetic-original",
            "published_status": "RECORDED_SYNTHETIC",
            "adapter_version": ADAPTER_VERSION,
            "normalization_version": NORMALIZATION_VERSION,
            "feature_version": FEATURE_VERSION,
            "source_scope": TARGET_SOURCE_STREAM,
        }
    if not isinstance(binding, UnitBinding):
        raise ValueError("binding: expected UnitBinding")
    if binding.runtime_scope != "TEST_ONLY_OFFLINE":
        raise ValueError("binding.runtime_scope: only TEST_ONLY_OFFLINE is enabled")
    if (
        binding.venue != TARGET_VENUE
        or binding.market_type != TARGET_MARKET_TYPE
        or binding.instrument_id != TARGET_INSTRUMENT_ID
        or binding.symbol != TARGET_SYMBOL
        or binding.source_stream != TARGET_SOURCE_STREAM
        or binding.stream_name != TARGET_WS_STREAM
        or binding.st_if_present != 1
        or binding.quantity_unit != "BTC"
        or binding.normal_quantity_unit != "BTC"
        or binding.price_unit != "USDT/BTC"
        or binding.notional_unit != "USDT"
        or binding.quantity_multiplier_decimal != "1"
        or binding.price_multiplier_decimal != "1"
    ):
        raise ValueError("binding: unsupported BTCUSDT aggTrade unit binding")
    return {
        "mode": "TEST_ONLY_LIVE",
        "accepted_raw_evidence_kind": binding.runtime_scope,
        "metadata_evidence_kind": binding.runtime_scope,
        "unit_evidence_id": binding.unit_evidence_id,
        "evidence_version": binding.evidence_version,
        "evidence_sha256": binding.evidence_sha256,
        "approval_id": binding.approval_id,
        "approval_kind": binding.approval_kind,
        "metadata_version": binding.current_metadata_sha256,
        "dataset_revision": f"test-only-live:{binding.approval_id}",
        "published_status": "RECORDED_TEST_ONLY",
        "binding_scope": binding.runtime_scope,
        "adapter_version": BOUND_ADAPTER_VERSION,
        "normalization_version": BOUND_NORMALIZATION_VERSION,
        "feature_version": BOUND_FEATURE_VERSION,
        "source_scope": f"{TARGET_SOURCE_STREAM}:test_only_offline",
    }


class Core:
    """Sequential immutable cutoff snapshots; dedup rebuilt from trusted raw on restart."""

    def __init__(
        self,
        config: dict[str, Any],
        metadata: dict[str, Any],
        *,
        binding: UnitBinding | None = None,
    ):
        if config.get("time_view", "as_received") != "as_received":
            raise ValueError("unsupported time_view")
        self.config = deepcopy(config)
        self.metadata = deepcopy(metadata)
        self.windows = config.get("window_seconds", [30])
        if not self.windows or any(type(w) is not int or w <= 0 for w in self.windows):
            raise ValueError("window_seconds: positive integers required")
        self.binding = binding
        self.unit_policy = _unit_policy(self.config, self.metadata, binding)
        self.normalized: list[dict[str, Any]] = []
        self.quarantine: list[dict[str, Any]] = []
        self.features: list[dict[str, Any]] = []
        self.pending: dict[int, list[dict[str, Any]]] = {}
        self.seen: dict[tuple[str, ...], dict[str, Any]] = {}
        self.conflicts: list[tuple[int, int]] = []
        self.errors: list[int] = []
        self.gaps: list[list[int | None]] = []
        self.start: int | None = None
        self.last_time: int | None = None
        self.cursor = 0
        self.fatal = False
        self.session: str | None = None
        self.last_cutoff = -1
        self.history: list[dict[str, Any]] = []
        # (receipt sequence, observed receive time, first economic receive time, flags)
        # Keep receipt-level knowledge separately from immutable unique trades.
        self.quality_observations: list[tuple[int, int, int, tuple[str, ...]]] = []

    def process(self, row: dict[str, Any]) -> None:
        row = deepcopy(row)
        seq = integer(row.get("ingest_sequence"), "ingest_sequence")
        now = integer(row.get("ts_receive_ns"), "ts_receive_ns")
        if seq <= self.cursor:
            raise ValueError("ingest_sequence: must strictly increase")
        if self.last_time is not None and now < self.last_time:
            self.fatal = True
            raise ValueError("CLOCK_UNCERTAIN: receive clock regressed")
        self.cursor, self.last_time = seq, now
        self.history.append(deepcopy(row))
        kind = row["kind"]
        if kind == "SESSION_OPEN":
            if self.session is not None:
                raise ValueError("session already open")
            self.session = row["session_id"]
            if self.start is None:
                self.start = now
        elif kind == "SESSION_CLOSE":
            if self.session != row["session_id"]:
                raise ValueError("session close mismatch")
            self.session = None
            if not self.gaps or self.gaps[-1][1] is not None:
                self.gaps.append([now, None])
        elif kind == "GAP_OPEN":
            if self.gaps and self.gaps[-1][1] is None:
                raise ValueError("gap already open")
            self.gaps.append([now, None])
        elif kind == "GAP_CLOSE":
            if not self.gaps or self.gaps[-1][1] is not None:
                raise ValueError("no open gap")
            self.gaps[-1][1] = now
        elif kind in ("CLOCK_UNCERTAIN", "STORAGE_ERROR"):
            self.fatal = True
        elif kind == "RAW":
            if self.session != row["session_id"]:
                raise ValueError("RAW outside active session")
            self._raw(row)
        elif kind == "FEATURE_CUTOFF":
            cutoff = integer(row.get("cutoff_ns"), "cutoff_ns")
            high = integer(row.get("input_cursor"), "input_cursor")
            if cutoff > now or high >= seq or cutoff <= self.last_cutoff:
                raise ValueError("invalid cutoff/high water/order")
            self.last_cutoff = cutoff
            snapshot = self
            if high < seq - 1:
                snapshot = Core(self.config, self.metadata, binding=self.binding)
                for previous in self.history:
                    if previous["ingest_sequence"] <= high and previous["kind"] not in (
                        "FEATURE_CUTOFF",
                        "FEATURE_PUBLISHED",
                    ):
                        snapshot.process(previous)
            self.pending[cutoff] = [snapshot._window(cutoff, high, w) for w in self.windows]
        elif kind == "FEATURE_PUBLISHED":
            cutoff = row["cutoff_ns"]
            available = integer(row.get("feature_available_ns"), "feature_available_ns")
            if cutoff not in self.pending or available < cutoff or available != now:
                raise ValueError("invalid historical publication")
            values = self.pending.pop(cutoff)
            if any(available < value["dependency_available_ns"] for value in values):
                raise ValueError("publication precedes dependency availability")
            for value in values:
                value["feature_available_ns"] = available
                value["publication_status"] = self.unit_policy["published_status"]
                self.features.append(value)
        elif kind not in ("SUBSCRIBED", "RECOVERY", "UNCERTAIN_GAP"):
            raise ValueError(f"unsupported control kind: {kind}")
        elif kind == "UNCERTAIN_GAP":
            self.gaps.append([now, None])

    def _raw(self, row: dict[str, Any]) -> None:
        try:
            trade = self._normalize(row)
        except (ProbeError, ValueError, KeyError, ArithmeticError) as exc:
            upstream = row.get("quality_flags", [])
            known = (
                sorted(
                    {flag for flag in upstream if isinstance(flag, str) and flag in QUALITY_FLAGS}
                )
                if isinstance(upstream, list)
                else []
            )
            flags = sorted(set(known) | {"SOURCE_SCHEMA_CHANGED"})
            self.quarantine.append(
                {
                    **row,
                    "quality_flags": flags,
                    "reason": str(exc),
                    "upstream_quality_flags_json": json.dumps(upstream, sort_keys=True),
                    "normalization_version": NORMALIZATION_VERSION,
                }
            )
            self.quality_observations.append(
                (row["ingest_sequence"], row["ts_receive_ns"], row["ts_receive_ns"], tuple(flags))
            )
            self.errors.append(row["ts_receive_ns"])
            return
        key = tuple(
            str(trade[k]) for k in ("venue", "market_type", "symbol", "record_kind", "agg_trade_id")
        )
        previous = self.seen.get(key)
        if trade["quality_flags"]:
            self.quality_observations.append(
                (
                    trade["ingest_sequence"],
                    trade["ts_receive_ns"],
                    previous["ts_receive_ns"] if previous is not None else trade["ts_receive_ns"],
                    tuple(trade["quality_flags"]),
                )
            )
        if previous is not None:
            fields = (
                "price_quote",
                "quantity_native",
                "buyer_is_maker",
                "normal_quantity_native",
                "first_trade_id",
                "last_trade_id",
                "ts_trade_ns",
            )
            conflict = any(previous[k] != trade[k] for k in fields)
            flag = "DUPLICATE_CONFLICT" if conflict else "DUPLICATE"
            self.quarantine.append(
                {
                    **trade,
                    "quality_flags": sorted(set(trade["quality_flags"]) | {flag}),
                    "economic_ingest_sequence": previous["ingest_sequence"],
                }
            )
            if conflict:
                self.conflicts.append((row["ts_receive_ns"], previous["ts_receive_ns"]))
            return
        self.seen[key] = trade
        self.normalized.append(trade)

    def _normalize(self, row: dict[str, Any]) -> dict[str, Any]:
        flags = incoming_quality(row)
        for field, expected in (
            ("venue", "binance"),
            ("market_type", "um_perpetual"),
            ("source_stream", "um_agg_trade"),
        ):
            if row.get(field, expected) != expected:
                raise ProbeError(f"{field}: unsupported source")
        raw = object_json(row["payload"].encode())
        if "data" in raw:
            if raw.get("stream") != TARGET_WS_STREAM or not isinstance(raw["data"], dict):
                raise ProbeError("stream: unexpected wrapper")
            raw = raw["data"]
        if raw.get("e") != "aggTrade" or raw.get("s") != TARGET_SYMBOL:
            raise ProbeError("e/s: unsupported source")
        if "st" in raw and integer(raw.get("st"), "st") != 1:
            raise ProbeError("st: unsupported source")
        if type(raw.get("m")) is not bool:
            raise ProbeError("m: boolean required")
        price, quantity = exact(raw.get("p"), "p"), exact(raw.get("q"), "q")
        normal = exact(raw["nq"], "nq", True) if "nq" in raw else None
        if normal is not None and normal > quantity:
            raise ProbeError("nq: exceeds q")
        first, last = integer(raw.get("f"), "f"), integer(raw.get("l"), "l")
        if first > last:
            raise ProbeError("f/l: reversed range")
        unit = row.get("source_time_unit", "ms")
        if unit not in ("ms", "us", "ns"):
            raise ProbeError("source_time_unit: unsupported")
        multiplier = {"ms": 1_000_000, "us": 1000, "ns": 1}[unit]
        event = integer(raw.get("E"), "E") * multiplier
        time = integer(raw.get("T"), "T") * multiplier
        if max(event, time, row["ts_receive_ns"]) > 2**63 - 1:
            raise ProbeError("timestamp: outside int64")
        meta = self.metadata
        verified = self._unit_verified(row, flags)
        if not verified:
            flags = sorted(set(flags) | {"UNIT_UNKNOWN"})
        with localcontext() as context:
            context.prec = 80
            for name, value in (("price_tick", price), ("quantity_step", quantity)):
                if name in meta and value % exact(meta[name], name) != 0:
                    raise ProbeError(f"{name}: misaligned")
        if "minimum_quantity" in meta and quantity < exact(
            meta["minimum_quantity"], "minimum_quantity"
        ):
            raise ProbeError("q: below minimum_quantity")
        with localcontext() as context:
            context.prec = 80
            notional = price * quantity if verified else None
        live_provenance = (
            {
                "unit_binding_id": self.unit_policy["unit_evidence_id"],
                "unit_binding_scope": self.unit_policy["binding_scope"],
                "unit_evidence_version": self.unit_policy["evidence_version"],
                "unit_evidence_sha256": self.unit_policy["evidence_sha256"],
                "unit_approval_id": self.unit_policy["approval_id"],
                "unit_approval_kind": self.unit_policy["approval_kind"],
            }
            if self.unit_policy["mode"] != "SYNTHETIC_DEFAULT"
            else {}
        )
        return {
            **{k: v for k, v in row.items() if k not in ("payload", "kind")},
            "venue": TARGET_VENUE,
            "market_type": TARGET_MARKET_TYPE,
            "symbol": TARGET_SYMBOL,
            "instrument_id": TARGET_INSTRUMENT_ID,
            "source_stream": TARGET_SOURCE_STREAM,
            "source_origin": row.get("source_origin", "live_ws"),
            "source_time_unit": unit,
            "schema_version": SCHEMA_VERSION,
            "adapter_version": self.unit_policy["adapter_version"],
            "normalization_version": self.unit_policy["normalization_version"],
            "metadata_version": meta.get("metadata_version", "UNKNOWN"),
            "unit_evidence_id": meta.get("unit_evidence_id"),
            "quantity_unit": "BTC" if verified else "UNKNOWN",
            "record_kind": "AGG_TRADE",
            "agg_trade_id": str(integer(raw.get("a"), "a")),
            "first_trade_id": str(first),
            "last_trade_id": str(last),
            "price_quote": price,
            "quantity_native": quantity,
            "quantity_base": quantity if verified else None,
            "normal_quantity_native": normal,
            "normal_quantity_base": normal if verified else None,
            "notional_quote": notional,
            "buyer_is_maker": raw["m"],
            "aggressor_side": "SELL" if raw["m"] else "BUY",
            "signed_quantity_base": (quantity.copy_negate() if raw["m"] else quantity)
            if verified
            else None,
            "ts_event_ns": event,
            "ts_trade_ns": time,
            "symbol_type_raw": raw.get("st"),
            "source_coverage": "AGGREGATED_MARKET_TRADES_NOT_FULL_LEDGER",
            "quality_flags": flags,
            **live_provenance,
        }

    def _unit_verified(self, row: dict[str, Any], flags: list[str]) -> bool:
        if "UNIT_UNKNOWN" in flags:
            return False
        policy = self.unit_policy
        meta = self.metadata
        if policy["mode"] == "SYNTHETIC_DEFAULT":
            return (
                meta.get("quantity_unit") == "BTC"
                and meta.get("evidence_kind") == "SYNTHETIC"
                and str(meta.get("unit_evidence_id", "")).startswith("SYNTHETIC:")
                and row.get("evidence_kind") == "SYNTHETIC"
            )
        for field, expected in (
            ("venue", TARGET_VENUE),
            ("market_type", TARGET_MARKET_TYPE),
            ("instrument_id", TARGET_INSTRUMENT_ID),
            ("source_stream", TARGET_SOURCE_STREAM),
            ("metadata_version", policy["metadata_version"]),
            ("evidence_kind", policy["accepted_raw_evidence_kind"]),
        ):
            if row.get(field) != expected:
                return False
        return (
            row.get("source_origin") == "live_ws"
            and meta.get("metadata_version") == policy["metadata_version"]
            and meta.get("quantity_unit") == "BTC"
            and meta.get("evidence_kind") == policy["metadata_evidence_kind"]
            and meta.get("unit_evidence_id") == policy["unit_evidence_id"]
        )

    def _unit_policy_ready(self) -> bool:
        policy = self.unit_policy
        if policy["mode"] == "SYNTHETIC_DEFAULT":
            return (
                self.metadata.get("quantity_unit") == "BTC"
                and self.metadata.get("evidence_kind") == "SYNTHETIC"
                and str(self.metadata.get("unit_evidence_id", "")).startswith("SYNTHETIC:")
            )
        return (
            self.metadata.get("quantity_unit") == "BTC"
            and self.metadata.get("evidence_kind") == policy["metadata_evidence_kind"]
            and self.metadata.get("unit_evidence_id") == policy["unit_evidence_id"]
            and self.metadata.get("metadata_version") == policy["metadata_version"]
        )

    def _window(self, cutoff: int, high: int, window: int) -> dict[str, Any]:
        left = cutoff - window * NS
        selected = [
            r
            for r in self.normalized
            if left <= r["ts_receive_ns"] < cutoff and r["ingest_sequence"] <= high
        ]
        reasons: list[str] = []
        if self.start is None or self.start > left:
            reasons.append("WARMUP")
        if self.fatal:
            reasons.append("FATAL_HEALTH")
        if any(
            start is not None and start < cutoff and (end is None or end > left)
            for start, end in self.gaps
        ):
            reasons.append("GAP_KNOWN")
        if any(left <= t < cutoff for t in self.errors):
            reasons.append("SOURCE_SCHEMA_CHANGED")
        if any(
            observed <= cutoff and (original >= left or observed >= left)
            for observed, original in self.conflicts
        ):
            reasons.append("DUPLICATE_CONFLICT")
        if not self._unit_policy_ready() or any(r["quantity_base"] is None for r in selected):
            reasons.append("UNIT_UNKNOWN")
        upstream = {
            flag
            for sequence, observed, economic_time, flags in self.quality_observations
            if sequence <= high
            and observed <= cutoff
            and (left <= observed < cutoff or left <= economic_time < cutoff)
            for flag in flags
        }
        # No inferred gap interval: use only the known receipt/economic points.
        # A later receipt may degrade a current window, never an earlier snapshot.
        invalid = bool(reasons) or bool(upstream & FLOW_INVALID_FLAGS)
        reasons.extend(flag for flag in sorted(upstream) if flag not in reasons)
        buy = sell = volume = delta = imbalance = None
        count = None
        if not invalid:
            with localcontext() as context:
                context.prec = 80
                buy = sum(
                    (r["quantity_base"] for r in selected if r["aggressor_side"] == "BUY"),
                    Decimal(0),
                )
                sell = sum(
                    (r["quantity_base"] for r in selected if r["aggressor_side"] == "SELL"),
                    Decimal(0),
                )
                volume, delta = buy + sell, buy - sell
                imbalance = float(delta / volume) if volume else None
            count = len(selected)
        live_provenance = (
            {
                "unit_binding_id": self.unit_policy["unit_evidence_id"],
                "unit_binding_scope": self.unit_policy["binding_scope"],
                "unit_evidence_version": self.unit_policy["evidence_version"],
                "unit_evidence_sha256": self.unit_policy["evidence_sha256"],
                "unit_approval_id": self.unit_policy["approval_id"],
                "unit_approval_kind": self.unit_policy["approval_kind"],
            }
            if self.unit_policy["mode"] != "SYNTHETIC_DEFAULT"
            else {}
        )
        return {
            "feature_cutoff_ns": cutoff,
            "feature_available_ns": None,
            "dependency_available_ns": max([cutoff, *[r["ts_receive_ns"] for r in selected]]),
            "window_seconds": window,
            "buy_volume_base": buy,
            "sell_volume_base": sell,
            "volume_base": volume,
            "trade_delta_base": delta,
            "trade_imbalance": imbalance,
            "agg_message_count": count,
            "quality_flags": reasons,
            "data_valid": not invalid,
            "time_view": "as_received",
            "feature_version": self.unit_policy["feature_version"],
            "schema_version": SCHEMA_VERSION,
            "input_cursor": high,
            "instrument_id": TARGET_INSTRUMENT_ID,
            "source_scope": self.unit_policy["source_scope"],
            "dataset_revision": self.unit_policy["dataset_revision"],
            "publication_status": "UNVERIFIED",
            **live_provenance,
        }

    @property
    def pending_features(self) -> list[dict[str, Any]]:
        return deepcopy([row for values in self.pending.values() for row in values])
