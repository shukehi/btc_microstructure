"""Read-only R2-04 replay; no clocks, transport, recovery mutations or output files."""

from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
from pathlib import Path
from typing import Any

from .bounded_transport import payload_for_core
from .offline_core import Core
from .offline_storage import Store, StorageError, _arrow_table, _json_line, _read_json
from .probe import object_json
from .unit_binding import STREAM, load_unit_evidence, resolve_test_only_unit_binding

JsonRow = dict[str, Any]


class ReadOnlyStore(Store):
    """Use original manifest validation without calling its recovering constructor."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._cursor: int | None = None
        self._trusted_manifests: list[JsonRow] = []
        self.recovery_events: list[JsonRow] = []
        if (root / "writer-open").exists() and not (root / "writer-closed").exists():
            self.recovery_events.append(
                {"event_type": "UNCERTAIN_GAP", "reason": "NO_WRITER_CLOSE_ACK"}
            )
        trusted: set[str] = set()
        broken = False
        for path in sorted((root / "manifests").glob("manifest-*.json")):
            try:
                if broken:
                    raise StorageError("after untrusted prefix")
                if path.is_symlink():
                    raise StorageError("symlink manifest")
                manifest = _read_json(path)
                self._validate_manifest(manifest, self._cursor)
                self._trusted_manifests.append(manifest)
                trusted.add(str(manifest["path"]))
                if manifest["kind"] == "raw_control_segment":
                    self._cursor = manifest["cursor"]["after"]
            except (OSError, ValueError, KeyError, StorageError):
                broken = True
                self.recovery_events.append({"event_type": "UNCERTAIN_GAP", "manifest": path.name})
        for path in (root / "raw").glob("*"):
            if path.is_file() and str(path.relative_to(root)) not in trusted:
                self.recovery_events.append({"event_type": "UNCERTAIN_GAP", "raw": path.name})


def _decode_snapshot(item: JsonRow) -> bytes:
    data = base64.b64decode(item["base64"], validate=True)
    if hashlib.sha256(data).hexdigest() != item["sha256"]:
        raise StorageError("snapshot hash mismatch")
    return data


def _core_row(row: JsonRow) -> JsonRow:
    value = deepcopy(row)
    if value["kind"] == "RAW":
        if "payload_base64" in value:
            payload = base64.b64decode(value["payload_base64"], validate=True)
            if hashlib.sha256(payload).hexdigest() != value["payload_sha256"]:
                raise StorageError("raw application-byte hash mismatch")
            value["payload"], _ = payload_for_core(payload)
        elif "payload" in value:
            payload = value["payload"].encode()
            if hashlib.sha256(payload).hexdigest() != value["payload_sha256"]:
                raise StorageError("raw application-byte hash mismatch")
        else:
            raise StorageError("raw application bytes missing")
    return value


def replay_bounded(root: Path) -> JsonRow:
    before_digest = _tree_digest(root)
    context_path = root / "snapshots" / "run-context.json"
    if context_path.is_symlink():
        raise StorageError("symlink context")
    context = object_json(context_path.read_bytes())
    if context.get("schema_version") != "bounded-run-context-1":
        raise StorageError("unsupported bounded source")
    evidence_bytes = _decode_snapshot(context["unit_evidence"])
    metadata_bytes = _decode_snapshot(context["metadata_response"])
    evidence, evidence_hash = load_unit_evidence(evidence_bytes)
    binding = resolve_test_only_unit_binding(
        evidence=evidence,
        evidence_sha256=evidence_hash,
        current_metadata=object_json(metadata_bytes),
        approval=context["unit_approval"],
        source_stream=STREAM,
        symbol_type_raw=None,
    )
    config = context["config"]
    core = Core(
        {"time_view": "as_received", "window_seconds": config["window_seconds"]},
        dict(binding.core_metadata()),
        binding=binding,
    )
    store = ReadOnlyStore(root)
    for row in store.read_rows():
        if row["kind"] in {
            "SESSION_OPEN",
            "SESSION_CLOSE",
            "SUBSCRIBED",
            "RAW",
            "FEATURE_CUTOFF",
            "FEATURE_PUBLISHED",
            "GAP_OPEN",
            "GAP_CLOSE",
            "STORAGE_ERROR",
            "CLOCK_UNCERTAIN",
            "UNCERTAIN_GAP",
        }:
            core.process(_core_row(row))
    features = [*core.features, *core.pending_features]
    reconstructed = [core.normalized, core.quarantine, features]
    # Use the existing table codec in memory; do not create Parquet files in replay.
    replay_tables = [_arrow_table(rows).to_pylist() for rows in reconstructed]
    digest = _tables_digest(replay_tables)
    original_digest: str | None = None
    matches: bool | None = None
    try:
        original_tables = [
            store.read_table(name) for name in ("normalized", "quarantine", "features")
        ]
        original_digest = _tables_digest(original_tables)
        matches = original_tables == replay_tables
    except (OSError, StorageError):
        pass
    after_digest = _tree_digest(root)
    status = "PASS"
    if store.recovery_events or core.pending_features or matches is None:
        status = "PARTIAL"
    if matches is False or before_digest != after_digest:
        status = "FAIL"
    return {
        "status": status,
        "network_access": "DISABLED",
        "read_only": True,
        "input_unchanged": before_digest == after_digest,
        "trusted_cursor": store.cursor,
        "recovery_events": store.recovery_events,
        "logical_sha256": digest,
        "original_logical_sha256": original_digest,
        "replay_logical_sha256": digest,
        "replay_matches_original": matches,
        "normalized": core.normalized,
        "quarantine": core.quarantine,
        "features": features,
        "publication_source": "ORIGINAL_FEATURE_PUBLISHED_ONLY",
    }


def _tables_digest(tables: list[list[JsonRow]]) -> str:
    return hashlib.sha256(b"".join(_json_line({"rows": rows}) for rows in tables)).hexdigest()


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()
