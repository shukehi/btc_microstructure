"""Boundary for future runtime checks; no source evidence is inferred from config."""

from dataclasses import dataclass

from .config_models import ResearchConfig


@dataclass(frozen=True)
class CapabilityRequirement:
    name: str
    status: str = "UNVERIFIED"


def pending_capabilities(config: ResearchConfig) -> tuple[CapabilityRequirement, ...]:
    names = ["instrument_metadata", "protocol_routes_and_budget", "book_band_coverage"]
    if config.instruments.perpetual.oi_unit == "UNKNOWN":
        names.append("oi_absolute_unit_evidence")
    else:
        names.append("oi_declared_unit_evidence_review")
    if "event_time_revised" in [config.features.time_view, *config.features.optional_time_views]:
        names.append("event_time_per_source_support")
    if not all((config.streams.um_public, config.streams.um_market, config.streams.spot)):
        names.append("degraded_stream_scope_declaration")
    return tuple(CapabilityRequirement(name) for name in names)
