"""R1-03 offline configuration, synthetic runner, and deterministic replay."""

from __future__ import annotations

import hashlib
import json
import platform
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, cast

import yaml

from .config import ConfigError, UniqueKeyLoader
from .offline_core import ADAPTER_VERSION, FEATURE_VERSION, SCHEMA_VERSION, Core, exact
from .offline_storage import Store

JsonRow = dict[str, Any]
VENUE = "binance"
MARKET_TYPE = "um_perpetual"
INSTRUMENT_ID = "binance:um_perpetual:BTCUSDT"
SOURCE_STREAM = "um_agg_trade"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class OfflineConfig:
    """Strict synthetic-only R1-03 config; live collection cannot pass this schema."""

    def __init__(
        self,
        *,
        source_path: Path,
        grid_seconds: int,
        window_seconds: list[int],
        horizon_seconds: list[int],
        synthetic_input: Path,
        output_root: Path,
        run_id: str,
        metadata: JsonRow,
        max_events: int,
        max_fixture_bytes: int,
        max_output_grid_rows: int,
        min_free_bytes: int,
    ) -> None:
        self.source_path = source_path
        self.grid_seconds = grid_seconds
        self.window_seconds = window_seconds
        self.horizon_seconds = horizon_seconds
        self.synthetic_input = synthetic_input
        self.output_root = output_root
        self.run_id = run_id
        self.metadata = metadata
        self.max_events = max_events
        self.max_fixture_bytes = max_fixture_bytes
        self.max_output_grid_rows = max_output_grid_rows
        self.min_free_bytes = min_free_bytes

    @classmethod
    def load(cls, path: str | Path) -> OfflineConfig:
        source = Path(path)
        try:
            raw = yaml.load(source.read_text(encoding="utf-8"), Loader=UniqueKeyLoader)
        except ConfigError:
            raise
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise ConfigError("offline config: cannot read valid UTF-8 YAML") from exc
        if not isinstance(raw, dict):
            raise ConfigError("offline config: top-level mapping required")
        allowed = {
            "input_mode",
            "source_stream",
            "time_view",
            "grid_seconds",
            "window_seconds",
            "horizon_seconds",
            "synthetic_input",
            "output_root",
            "run_id",
            "metadata",
            "limits",
        }
        unknown = sorted(set(raw) - allowed)
        if unknown:
            raise ConfigError(f"offline config: unknown keys {', '.join(unknown)}")
        if raw.get("input_mode") != "synthetic":
            raise ConfigError(
                "input_mode: only synthetic is allowed; live collection is not enabled"
            )
        if raw.get("source_stream") != SOURCE_STREAM:
            raise ConfigError("source_stream: only um_agg_trade is supported in R1-03")
        if raw.get("time_view") != "as_received":
            raise ConfigError("time_view: only as_received is supported in R1-03")
        limits = _mapping(raw.get("limits", {}), "limits")
        _validate_limits(limits)
        metadata = _mapping(raw.get("metadata"), "metadata")
        _validate_metadata(metadata)
        grid_seconds = _positive_int(raw.get("grid_seconds"), "grid_seconds")
        return cls(
            source_path=source,
            grid_seconds=grid_seconds,
            window_seconds=_unique_positive(raw.get("window_seconds"), "window_seconds"),
            horizon_seconds=_unique_positive(raw.get("horizon_seconds"), "horizon_seconds"),
            synthetic_input=_relative_existing_file(
                source.parent, raw.get("synthetic_input"), "synthetic_input"
            ),
            output_root=_relative_path(source.parent, raw.get("output_root"), "output_root"),
            run_id=_run_id(raw.get("run_id")),
            metadata=metadata,
            max_events=_positive_int(limits.get("max_events", 100_000), "limits.max_events"),
            max_fixture_bytes=_positive_int(
                limits.get("max_fixture_bytes", 16 * 1024 * 1024),
                "limits.max_fixture_bytes",
            ),
            max_output_grid_rows=_positive_int(
                limits.get("max_output_grid_rows", 100_000),
                "limits.max_output_grid_rows",
            ),
            min_free_bytes=_nonnegative_int(
                limits.get("min_free_bytes", 2 * 1024 * 1024 * 1024),
                "limits.min_free_bytes",
            ),
        )

    def core_config(self) -> JsonRow:
        return {
            "time_view": "as_received",
            "grid_seconds": self.grid_seconds,
            "window_seconds": list(self.window_seconds),
            "horizon_seconds": list(self.horizon_seconds),
            "run_id": self.run_id,
            "limits": {
                "max_events": self.max_events,
                "max_fixture_bytes": self.max_fixture_bytes,
                "max_output_grid_rows": self.max_output_grid_rows,
                "min_free_bytes": self.min_free_bytes,
            },
        }

    def digest(self) -> str:
        return _sha256_text(_canonical_json({**self.core_config(), "metadata": self.metadata}))


def _mapping(value: object, field_name: str) -> JsonRow:
    if not isinstance(value, dict):
        raise ConfigError(f"{field_name}: mapping required")
    return cast(JsonRow, value)


def _validate_limits(limits: JsonRow) -> None:
    allowed = {"max_events", "max_fixture_bytes", "max_output_grid_rows", "min_free_bytes"}
    unknown = sorted(set(limits) - allowed)
    if unknown:
        raise ConfigError(f"limits: unknown keys {', '.join(unknown)}")


def _positive_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(f"{field_name}: positive integer required")
    return value


def _unique_positive(value: object, field_name: str) -> list[int]:
    if not isinstance(value, list) or not value:
        raise ConfigError(f"{field_name}: nonempty list required")
    result = [_positive_int(item, field_name) for item in value]
    if len(result) != len(set(result)):
        raise ConfigError(f"{field_name}: duplicate values are not allowed")
    return result


def _run_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.strip() != value
        or any(c.isspace() for c in value)
    ):
        raise ConfigError("run_id: nonempty string without whitespace required")
    return value


def _relative_path(base: Path, value: object, field_name: str) -> Path:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ConfigError(f"{field_name}: local path string required")
    if "\0" in value or "://" in value or value.startswith("~"):
        raise ConfigError(f"{field_name}: only explicit local paths are allowed")
    path = Path(value)
    return path if path.is_absolute() else base / path


def _relative_existing_file(base: Path, value: object, field_name: str) -> Path:
    path = _relative_path(base, value, field_name)
    if not path.is_file():
        raise ConfigError(f"{field_name}: file does not exist")
    return path


def _validate_metadata(metadata: JsonRow) -> None:
    allowed = {
        "venue",
        "market_type",
        "instrument_id",
        "source_stream",
        "symbol",
        "metadata_version",
        "quantity_unit",
        "unit_evidence_id",
        "evidence_kind",
        "price_tick",
        "quantity_step",
        "minimum_quantity",
    }
    unknown = sorted(set(metadata) - allowed)
    if unknown:
        raise ConfigError(f"metadata: unknown keys {', '.join(unknown)}")
    expected_identity = {
        "venue": VENUE,
        "market_type": MARKET_TYPE,
        "instrument_id": INSTRUMENT_ID,
        "source_stream": SOURCE_STREAM,
        "symbol": "BTCUSDT",
    }
    for field, expected in expected_identity.items():
        if metadata.get(field) != expected:
            raise ConfigError(f"metadata.{field}: expected {expected}")
    for field in (
        "metadata_version",
        "quantity_unit",
        "unit_evidence_id",
        "evidence_kind",
        "price_tick",
        "quantity_step",
        "minimum_quantity",
    ):
        if field not in metadata:
            raise ConfigError(f"metadata.{field}: required")
    if metadata["quantity_unit"] not in {"BTC", "UNKNOWN"}:
        raise ConfigError("metadata.quantity_unit: expected BTC or UNKNOWN")
    if metadata["evidence_kind"] != "SYNTHETIC":
        raise ConfigError("metadata.evidence_kind: only SYNTHETIC is allowed")
    if metadata["quantity_unit"] == "BTC" and not str(metadata["unit_evidence_id"]).startswith(
        "SYNTHETIC:"
    ):
        raise ConfigError("metadata.unit_evidence_id: SYNTHETIC evidence id required")
    for field in ("price_tick", "quantity_step", "minimum_quantity"):
        value = metadata[field]
        if not isinstance(value, str):
            raise ConfigError(f"metadata.{field}: positive decimal string required")
        try:
            parsed = exact(value, f"metadata.{field}")
        except Exception as exc:
            raise ConfigError(f"metadata.{field}: positive decimal string required") from exc
        if parsed <= 0:
            raise ConfigError(f"metadata.{field}: positive decimal string required")


def load_fixture(path: Path, *, max_bytes: int, max_events: int) -> JsonRow:
    if path.stat().st_size > max_bytes:
        raise ConfigError("synthetic fixture: exceeds configured max_fixture_bytes")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ConfigError("synthetic fixture: cannot read valid JSON") from exc
    if not isinstance(payload, dict):
        raise ConfigError("synthetic fixture: top-level object required")
    events = payload.get("events")
    if not isinstance(events, list):
        raise ConfigError("synthetic fixture: events list required")
    if len(events) > max_events:
        raise ConfigError("synthetic fixture: exceeds configured max_events")
    return cast(JsonRow, payload)


def fixture_rows(config: OfflineConfig) -> list[JsonRow]:
    fixture = load_fixture(
        config.synthetic_input, max_bytes=config.max_fixture_bytes, max_events=config.max_events
    )
    base_events: list[JsonRow] = []
    for value in cast(list[Any], fixture["events"]):
        if not isinstance(value, dict):
            raise ConfigError("synthetic fixture: event object required")
        event = cast(JsonRow, value)
        kind = event.get("kind")
        if kind == "RAW":
            payload = event.get("payload")
            payload_text = _canonical_json(payload) if isinstance(payload, dict) else str(payload)
            row = _base_row(event)
            row.update(
                {
                    "payload": payload_text,
                    "raw_ref": _raw_ref(event.get("raw_ref")),
                    "source_origin": "live_ws",
                    "source_time_unit": event.get("source_time_unit", "ms"),
                    "metadata_version": config.metadata["metadata_version"],
                    "evidence_kind": "SYNTHETIC",
                    "quality_flags": event.get("quality_flags", []),
                }
            )
            base_events.append(row)
        elif kind in {
            "SESSION_OPEN",
            "SESSION_CLOSE",
            "GAP_OPEN",
            "GAP_CLOSE",
            "FEATURE_CUTOFF",
            "FEATURE_PUBLISHED",
            "CLOCK_UNCERTAIN",
            "STORAGE_ERROR",
            "SUBSCRIBED",
            "RECOVERY",
            "UNCERTAIN_GAP",
        }:
            row = _base_row(event)
            row.update({key: item for key, item in event.items() if key not in row})
            base_events.append(row)
        else:
            raise ConfigError(f"synthetic fixture: unsupported event kind {kind}")
    _validate_explicit_order(base_events)
    rows = _expand_feature_schedule(fixture, base_events, config)
    if len(rows) > config.max_events:
        raise ConfigError("synthetic fixture: expanded event count exceeds configured max_events")
    return rows


def _expand_feature_schedule(
    fixture: JsonRow, base_events: list[JsonRow], config: OfflineConfig
) -> list[JsonRow]:
    schedule = fixture.get("feature_schedule")
    if schedule is None:
        return sorted(base_events, key=lambda row: cast(int, row["ingest_sequence"]))
    if not isinstance(schedule, dict):
        raise ConfigError("feature_schedule: mapping required")
    start_ns = _positive_int(schedule.get("start_ns"), "feature_schedule.start_ns")
    end_ns = _positive_int(schedule.get("end_ns"), "feature_schedule.end_ns")
    grid_ns = config.grid_seconds * 1_000_000_000
    declared_grid = schedule.get("grid_ns")
    if (
        declared_grid is not None
        and _positive_int(declared_grid, "feature_schedule.grid_ns") != grid_ns
    ):
        raise ConfigError("feature_schedule.grid_ns: must match config.grid_seconds")
    publish_delay_ns = _positive_int(
        schedule.get("publish_delay_ns"), "feature_schedule.publish_delay_ns"
    )
    if end_ns < start_ns:
        raise ConfigError("feature_schedule.end_ns: must be >= start_ns")
    cutoff_count = ((end_ns - start_ns) // grid_ns) + 1
    if len(base_events) + 2 * cutoff_count > config.max_events:
        raise ConfigError("synthetic fixture: expanded event count exceeds configured max_events")
    controls: list[JsonRow] = []
    explicit = [
        {
            **dict(row),
            "source_ingest_sequence": cast(int, row["ingest_sequence"]),
            "sequence_origin": "fixture_explicit",
        }
        for row in base_events
    ]
    cutoff = start_ns
    control_index = 0
    while cutoff <= end_ns:
        controls.extend(
            [
                {
                    "kind": "FEATURE_CUTOFF",
                    "schema_version": SCHEMA_VERSION,
                    "adapter_version": ADAPTER_VERSION,
                    "venue": VENUE,
                    "market_type": MARKET_TYPE,
                    "instrument_id": INSTRUMENT_ID,
                    "source_stream": SOURCE_STREAM,
                    "session_id": "synthetic-session",
                    "ts_receive_ns": cutoff,
                    "ingest_sequence": 0,
                    "cutoff_ns": cutoff,
                    "input_cursor": 0,
                    "input_cursor_source_ingest_sequence": 0,
                    "sequence_origin": "feature_schedule",
                    "schedule_sequence": control_index,
                },
                {
                    "kind": "FEATURE_PUBLISHED",
                    "schema_version": SCHEMA_VERSION,
                    "adapter_version": ADAPTER_VERSION,
                    "venue": VENUE,
                    "market_type": MARKET_TYPE,
                    "instrument_id": INSTRUMENT_ID,
                    "source_stream": SOURCE_STREAM,
                    "session_id": "synthetic-session",
                    "ts_receive_ns": cutoff + publish_delay_ns,
                    "ingest_sequence": 0,
                    "cutoff_ns": cutoff,
                    "feature_available_ns": cutoff + publish_delay_ns,
                    "sequence_origin": "feature_schedule",
                    "schedule_sequence": control_index + 1,
                },
            ]
        )
        control_index += 2
        cutoff += grid_ns
    merged = sorted(
        [*explicit, *controls],
        key=_scheduled_event_key,
    )
    generated: list[JsonRow] = []
    next_sequence = 1
    last_input_cursor = 0
    last_input_source_sequence = 0
    for row in merged:
        if row["kind"] == "FEATURE_CUTOFF":
            row["input_cursor"] = last_input_cursor
            row["input_cursor_source_ingest_sequence"] = last_input_source_sequence
        row["ingest_sequence"] = next_sequence
        next_sequence += 1
        generated.append(row)
        if row.get("sequence_origin") == "fixture_explicit" and row["kind"] not in (
            "FEATURE_CUTOFF",
            "FEATURE_PUBLISHED",
        ):
            last_input_cursor = cast(int, row["ingest_sequence"])
            last_input_source_sequence = cast(int, row["source_ingest_sequence"])
    return generated


def _validate_explicit_order(rows: list[JsonRow]) -> None:
    ordered = sorted(rows, key=lambda row: cast(int, row["ingest_sequence"]))
    previous_sequence = 0
    previous_time: int | None = None
    for row in ordered:
        sequence = cast(int, row["ingest_sequence"])
        receive_ns = cast(int, row["ts_receive_ns"])
        if sequence <= previous_sequence:
            raise ConfigError("synthetic fixture: explicit ingest_sequence must strictly increase")
        if previous_time is not None and receive_ns < previous_time:
            raise ConfigError(
                "synthetic fixture: explicit receive time regresses by ingest_sequence"
            )
        previous_sequence = sequence
        previous_time = receive_ns


def _event_priority(kind: str) -> int:
    if kind == "FEATURE_CUTOFF":
        return 0
    if kind == "FEATURE_PUBLISHED":
        return 2
    return 1


def _scheduled_event_key(row: JsonRow) -> tuple[int, int, int]:
    origin = row.get("sequence_origin")
    if origin == "fixture_explicit":
        return (cast(int, row["ts_receive_ns"]), 1, cast(int, row["source_ingest_sequence"]))
    return (
        cast(int, row["ts_receive_ns"]),
        _event_priority(cast(str, row["kind"])),
        cast(int, row["schedule_sequence"]),
    )


def _base_row(event: JsonRow) -> JsonRow:
    sequence = _positive_int(event.get("ingest_sequence"), "ingest_sequence")
    receive_ns = _nonnegative_int(event.get("ts_receive_ns"), "ts_receive_ns")
    session = event.get("session_id", "synthetic-session")
    if not isinstance(session, str) or not session:
        raise ConfigError("session_id: nonempty string required")
    return {
        "kind": event.get("kind"),
        "schema_version": SCHEMA_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "venue": VENUE,
        "market_type": MARKET_TYPE,
        "instrument_id": INSTRUMENT_ID,
        "source_stream": SOURCE_STREAM,
        "ts_receive_ns": receive_ns,
        "ingest_sequence": sequence,
        "session_id": session,
    }


def _raw_ref(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigError("raw_ref: nonempty string required")
    return value


def _nonnegative_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConfigError(f"{field_name}: non-negative integer required")
    return value


def run_synthetic(config: OfflineConfig, output_root: Path | None = None) -> JsonRow:
    root = output_root or config.output_root
    _ensure_new_output(root)
    metadata = _run_metadata(config)
    store = Store(root, run_metadata=metadata, min_free_bytes=config.min_free_bytes)
    core = Core(config.core_config(), config.metadata)
    for row in fixture_rows(config):
        store.append(row)
        store.flush()
        core.process(row)
    _write_outputs(store, core, config)
    summary = _summary(root, core, config, "PASS")
    _write_json(root / "summary.json", summary)
    return summary


def replay_run(config: OfflineConfig, input_root: Path, output_root: Path | None = None) -> JsonRow:
    if not input_root.exists():
        raise ConfigError("replay input: path does not exist")
    root = output_root or input_root / "replay"
    _ensure_new_output(root)
    replay_started_ns = time.perf_counter_ns()
    store = Store(
        root,
        run_metadata={**_run_metadata(config), "replay_of": str(input_root)},
        min_free_bytes=config.min_free_bytes,
    )
    core = Core(config.core_config(), config.metadata)
    source = Store(input_root)
    source_rows = source.read_rows()
    _validate_replay_source(config, input_root, source_rows)
    for row in source_rows:
        core.process(row)
    _write_outputs(store, core, config)
    original_features = source.read_table("features")
    replay_features = store.read_table("features")
    original_normalized = source.read_table("normalized_trades")
    original_quarantine = source.read_table("quarantine")
    replay_normalized = store.read_table("normalized_trades")
    replay_quarantine = store.read_table("quarantine")
    original_digest = _logical_digest(
        [*original_features, *original_normalized, *original_quarantine]
    )
    replay_digest = _logical_digest([*replay_features, *replay_normalized, *replay_quarantine])
    recovery_events = list(source.recovery_events)
    status = "PASS" if original_digest == replay_digest and not recovery_events else "FAIL"
    summary = _summary(root, core, config, status)
    summary.update(
        {
            "input_root": str(input_root),
            "original_logical_sha256": original_digest,
            "replay_logical_sha256": replay_digest,
            "replay_elapsed_ns": time.perf_counter_ns() - replay_started_ns,
            "recovery_events": recovery_events,
            "feature_available_time_source": "FEATURE_PUBLISHED_LOG"
            if _has_feature_publish(source_rows)
            else "UNVERIFIED_NO_PUBLISH_LOG",
        }
    )
    _write_json(root / "summary.json", summary)
    return summary


def build_features(config: OfflineConfig, input_root: Path, output_root: Path) -> JsonRow:
    if not input_root.exists():
        raise ConfigError("build-features input: path does not exist")
    _ensure_new_output(output_root)
    build_started_ns = time.perf_counter_ns()
    store = Store(
        output_root,
        run_metadata={**_run_metadata(config), "build_from": str(input_root)},
        min_free_bytes=config.min_free_bytes,
    )
    core = Core(config.core_config(), config.metadata)
    source = Store(input_root)
    source_rows = source.read_rows()
    _validate_replay_source(config, input_root, source_rows)
    for row in source_rows:
        core.process(row)
    _write_outputs(store, core, config)
    recovery_events = list(source.recovery_events)
    status = "PASS" if not _has_raw_uncertainty(recovery_events) else "FAIL"
    logical_digest = _logical_digest(
        [
            *store.read_table("features"),
            *store.read_table("normalized_trades"),
            *store.read_table("quarantine"),
        ]
    )
    summary = _summary(output_root, core, config, status)
    summary.update(
        {
            "input_root": str(input_root),
            "build_elapsed_ns": time.perf_counter_ns() - build_started_ns,
            "build_logical_sha256": logical_digest,
            "recovery_events": recovery_events,
            "feature_available_time_source": "FEATURE_PUBLISHED_LOG"
            if _has_feature_publish(source_rows)
            else "UNVERIFIED_NO_PUBLISH_LOG",
        }
    )
    _write_json(output_root / "summary.json", summary)
    return summary


def _write_outputs(store: Store, core: Core, config: OfflineConfig) -> None:
    config_hash = config.digest()
    features = [_attach_hash(row, config_hash) for row in [*core.features, *core.pending_features]]
    if len(features) > config.max_output_grid_rows:
        raise ConfigError("features: exceeds configured max_output_grid_rows")
    store.write_table(
        "normalized_trades", [_attach_hash(row, config_hash) for row in core.normalized]
    )
    store.write_table("quarantine", [_attach_hash(row, config_hash) for row in core.quarantine])
    store.write_table("features", features)
    store.close()


def _validate_replay_source(config: OfflineConfig, input_root: Path, rows: list[JsonRow]) -> None:
    expected_digest = config.digest()
    for row in rows:
        if row.get("schema_version") != SCHEMA_VERSION:
            raise ConfigError("replay input: unsupported raw/control schema_version")
        if row.get("adapter_version") != ADAPTER_VERSION:
            raise ConfigError("replay input: unsupported adapter_version")
        for field, expected in (
            ("venue", VENUE),
            ("market_type", MARKET_TYPE),
            ("instrument_id", INSTRUMENT_ID),
            ("source_stream", SOURCE_STREAM),
        ):
            if row.get(field) != expected:
                raise ConfigError(f"replay input: {field} does not match offline config")
        if row.get("kind") == "RAW":
            if row.get("evidence_kind") != "SYNTHETIC":
                raise ConfigError("replay input: RAW evidence_kind must remain SYNTHETIC")
            if row.get("metadata_version") != config.metadata["metadata_version"]:
                raise ConfigError("replay input: RAW metadata_version does not match config")
    for manifest_path in sorted((input_root / "manifests").glob("manifest-*.json")):
        try:
            manifest = cast(JsonRow, json.loads(manifest_path.read_text(encoding="utf-8")))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ConfigError("replay input: cannot read committed manifest") from exc
        run_metadata = manifest.get("run_metadata", {})
        if not isinstance(run_metadata, dict):
            raise ConfigError("replay input: manifest run_metadata missing")
        if run_metadata.get("offline_config_sha256") != expected_digest:
            raise ConfigError("replay input: manifest config hash does not match replay config")
        if run_metadata.get("schema_version") != SCHEMA_VERSION:
            raise ConfigError("replay input: manifest schema version mismatch")
        if run_metadata.get("adapter_version") != ADAPTER_VERSION:
            raise ConfigError("replay input: manifest adapter version mismatch")
        if run_metadata.get("feature_version") != FEATURE_VERSION:
            raise ConfigError("replay input: manifest feature version mismatch")
        if run_metadata.get("evidence_kind") != "SYNTHETIC":
            raise ConfigError("replay input: manifest evidence kind mismatch")


def _attach_hash(row: JsonRow, config_hash: str) -> JsonRow:
    return {**row, "config_hash": config_hash}


def _run_metadata(config: OfflineConfig) -> JsonRow:
    return {
        "schema_version": SCHEMA_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "feature_version": FEATURE_VERSION,
        "run_id": config.run_id,
        "evidence_kind": "SYNTHETIC",
        "offline_config_sha256": config.digest(),
        "git_commit": _git_commit(),
        "requirements_dev_lock_sha256": sha256_file(Path("requirements-dev.lock"))
        if Path("requirements-dev.lock").is_file()
        else None,
        "source_module_sha256": {
            "offline": _module_sha("offline.py"),
            "offline_core": _module_sha("offline_core.py"),
            "offline_storage": _module_sha("offline_storage.py"),
        },
        "git_worktree_dirty": _git_dirty(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "pyarrow": _pyarrow_version(),
        "time_view": "as_received",
        "random_seed": 0,
        "fixture_path": str(config.synthetic_input),
        "fixture_sha256": sha256_file(config.synthetic_input),
        "source_stream": SOURCE_STREAM,
        "network_access": "DISABLED",
        "binance_real_rest": "BLOCKED_HISTORICAL",
        "binance_real_ws": "NOT_RUN",
    }


def _git_commit() -> str:
    try:
        process = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "UNKNOWN"
    return process.stdout.strip()


def _git_dirty() -> bool | None:
    try:
        process = subprocess.run(
            ["git", "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return bool(process.stdout.strip())


def _module_sha(name: str) -> str | None:
    path = Path(__file__).resolve().with_name(name)
    return sha256_file(path) if path.is_file() else None


def _pyarrow_version() -> str:
    try:
        import pyarrow as pa
    except ImportError:
        return "NOT_AVAILABLE"
    return str(pa.__version__)


def _summary(root: Path, core: Core, config: OfflineConfig, status: str) -> JsonRow:
    return {
        "status": status,
        "run_id": config.run_id,
        "output_root": str(root),
        "normalized_rows": len(core.normalized),
        "quarantine_rows": len(core.quarantine),
        "feature_rows": len(core.features) + len(core.pending_features),
        "offline_config_sha256": config.digest(),
        "limitations": {
            "real_binance_rest": "BLOCKED_HISTORICAL",
            "real_binance_ws": "NOT_RUN",
            "quantity_unit_for_live": "UNKNOWN_UNVERIFIED",
            "continuous_collection": "NOT_RUN",
        },
    }


def _logical_digest(rows: list[JsonRow]) -> str:
    return _sha256_text(
        _canonical_json(sorted((_normalize_row(row) for row in rows), key=_canonical_json))
    )


def _normalize_row(row: JsonRow) -> JsonRow:
    ignored = {"created_ns"}
    return {key: value for key, value in row.items() if key not in ignored}


def _has_feature_publish(rows: list[JsonRow]) -> bool:
    return any(row.get("kind") == "FEATURE_PUBLISHED" for row in rows)


def _has_raw_uncertainty(events: list[JsonRow]) -> bool:
    return any(event.get("event_type") == "UNCERTAIN_GAP" for event in events)


def _write_json(path: Path, payload: JsonRow) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )


def _ensure_new_output(root: Path) -> None:
    if root.exists() and any(root.iterdir()):
        raise ConfigError(f"output: refusing to overwrite nonempty directory {root}")


__all__ = [
    "build_features",
    "OfflineConfig",
    "fixture_rows",
    "load_fixture",
    "replay_run",
    "run_synthetic",
    "sha256_file",
]
