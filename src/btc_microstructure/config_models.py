"""Complete v0.1 config schema. Validation never verifies source capabilities."""

from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    AfterValidator,
    BeforeValidator,
    ConfigDict,
    Field,
    model_validator,
    field_validator,
    ValidationInfo,
)

PositiveInt = Annotated[int, Field(gt=0)]
NonNegativeInt = Annotated[int, Field(ge=0)]
Positive = Annotated[float, Field(gt=0, allow_inf_nan=False)]
Fraction = Annotated[float, Field(gt=0, lt=1, allow_inf_nan=False)]
Coverage = Annotated[float, Field(gt=0, le=1, allow_inf_nan=False)]
Text = Annotated[str, Field(min_length=1, pattern=r"\S")]
TimeView = Literal["as_received", "event_time_revised"]


def unique_positive(values: list[int]) -> list[int]:
    if not values or len(values) != len(set(values)):
        raise ValueError("must be nonempty and contain no duplicates")
    return values


Windows = Annotated[list[PositiveInt], Field(min_length=1)]


def exact_decimal(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValueError("use a decimal string or integer, never a float")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("invalid decimal string") from exc
    if not result.is_finite():
        raise ValueError("decimal must be finite")
    return result


ExactNonNegative = Annotated[Decimal, BeforeValidator(exact_decimal), Field(ge=0)]
ExactPositive = Annotated[Decimal, BeforeValidator(exact_decimal), Field(gt=0)]


def require_true(value: bool) -> bool:
    if not value:
        raise ValueError("must be true: required by the v0.1 integrity/research contract")
    return value


# Use strict bool first: Literal[True] alone would also accept integer 1.
RequiredTrue = Annotated[bool, AfterValidator(require_true)]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True)


class Project(Model):
    name: Text
    language: Text
    timezone: Text
    mode: Literal["research_read_only"]
    automatic_trading: bool

    @field_validator("automatic_trading")
    @classmethod
    def no_trading(cls, value: bool) -> bool:
        if value:
            raise ValueError("must be false: automatic trading is prohibited")
        return value

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("unknown timezone") from exc
        return value


class Spot(Model):
    venue: Literal["binance"]
    market_type: Literal["spot"]
    symbol: Literal["BTCUSDT"]
    expected_base_asset: Literal["BTC"]
    expected_quote_asset: Literal["USDT"]


class Perpetual(Model):
    venue: Literal["binance"]
    market_type: Literal["um_perpetual"]
    symbol: Literal["BTCUSDT"]
    expected_base_asset: Literal["BTC"]
    expected_quote_asset: Literal["USDT"]
    expected_margin_asset: Literal["USDT"]
    expected_contract_type: Literal["PERPETUAL"]
    oi_unit: Literal["UNKNOWN", "BTC", "CONTRACTS"]
    unit_evidence_id: Text | None

    @model_validator(mode="after")
    def evidence_required(self) -> Self:
        if self.oi_unit != "UNKNOWN" and self.unit_evidence_id is None:
            raise ValueError("unit_evidence_id is required for a declared oi_unit")
        return self


class Instruments(Model):
    perpetual: Perpetual
    spot: Spot


class Endpoints(Model):
    um_public_ws: Text
    um_market_ws: Text
    um_rest: Text
    spot_ws: Text
    spot_rest: Text

    @field_validator("um_public_ws", "um_market_ws", "spot_ws", "um_rest", "spot_rest")
    @classmethod
    def public_url(cls, value: str, info: ValidationInfo) -> str:
        scheme = "wss" if (info.field_name or "").endswith("ws") else "https"
        try:
            url = urlsplit(value)
            port = url.port
        except ValueError as exc:
            raise ValueError("invalid endpoint URL") from exc
        if (
            url.scheme != scheme
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or any(c.isspace() for c in value)
        ):
            raise ValueError(f"must be a public {scheme} URL without credentials/query/fragment")
        if port is not None and port <= 0:
            raise ValueError("invalid endpoint port")
        return value


class Streams(Model):
    um_public: list[Text]
    um_market: list[Text]
    spot: list[Text]
    liquidation_required_for_core: bool
    spot_full_depth_enabled: bool
    rpi_depth_enabled: bool

    @field_validator("um_public", "um_market", "spot")
    @classmethod
    def no_duplicate_streams(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("duplicate stream")
        return values


class Collector(Model):
    oi_poll_seconds: PositiveInt
    metadata_refresh_seconds: PositiveInt
    funding_info_refresh_seconds: PositiveInt
    funding_history_check_seconds: PositiveInt
    snapshot_limit: PositiveInt
    checkpoint_seconds: PositiveInt
    connection_rotate_seconds: PositiveInt
    reconnect_initial_seconds: PositiveInt
    reconnect_max_seconds: PositiveInt
    max_concurrent_oi_requests: PositiveInt
    protocol_probe_required: bool
    protocol_probe_completed: bool
    endpoint_budget_file: Text | None
    queue_max_records: PositiveInt
    queue_max_bytes: PositiveInt
    queue_warning_fraction: Fraction

    @model_validator(mode="after")
    def reconnect_order(self) -> Self:
        if self.reconnect_initial_seconds > self.reconnect_max_seconds:
            raise ValueError("reconnect_max_seconds must be >= reconnect_initial_seconds")
        return self


class Storage(Model):
    root: Text
    normalized_format: Literal["parquet"]
    compression: Literal["zstd"]
    raw_commit_interval_seconds: PositiveInt
    disk_warning_gib: Positive
    disk_stop_gib: Positive
    delete_raw_automatically: bool
    source_payload_preserved: RequiredTrue

    @model_validator(mode="after")
    def disk_order(self) -> Self:
        if self.disk_stop_gib >= self.disk_warning_gib:
            raise ValueError("disk_stop_gib must be less than disk_warning_gib")
        return self


class Features(Model):
    time_view: TimeView
    optional_time_views: list[TimeView]
    grid_seconds: PositiveInt
    window_boundary: Literal["left_closed_right_open"]
    trade_lookbacks_seconds: Windows
    ofi_lookbacks_seconds: Windows
    oi_lookbacks_seconds: Windows
    depth_bps: Annotated[list[Annotated[int, Field(gt=0, lt=10000)]], Field(min_length=1)]
    ofi_source: Literal["validated_standard_l2_observations"]
    cvd_reset: Literal["utc_day_or_new_continuous_segment"]
    settled_funding_z_lookback_days: PositiveInt
    settled_funding_z_min_unique_events: PositiveInt
    settled_funding_z_same_interval_only: bool
    rolling_std_ddof: NonNegativeInt
    quantile_method: Literal["linear"]

    @field_validator(
        "trade_lookbacks_seconds", "ofi_lookbacks_seconds", "oi_lookbacks_seconds", "depth_bps"
    )
    @classmethod
    def unique_windows(cls, values: list[int]) -> list[int]:
        return unique_positive(values)

    @field_validator("optional_time_views")
    @classmethod
    def unique_views(cls, values: list[TimeView]) -> list[TimeView]:
        if len(values) != len(set(values)):
            raise ValueError("duplicate time view")
        return values


class Quality(Model):
    max_bbo_age_ms: PositiveInt
    max_book_age_ms: PositiveInt
    max_mark_index_age_ms: PositiveInt
    max_oi_poll_age_ms: PositiveInt
    max_oi_source_observation_age_ms: PositiveInt
    max_cross_market_quote_skew_ms: PositiveInt
    unknown_as_null: RequiredTrue
    known_gap_invalidates_flow_window: RequiredTrue
    require_complete_book_price_band: bool
    require_complete_path_for_mfe_mae: bool
    clock_uncertain_disables_lead_lag: bool


class Absorption(Model):
    lookback_seconds: PositiveInt
    high_side_volume_quantile: Fraction
    low_past_excursion_quantile: Fraction
    low_response_quantile: Fraction
    require_oi_expansion: bool
    require_l2_confirmation: bool


class States(Model):
    enabled: bool
    context_lookback_seconds: PositiveInt
    dead_zone_abs_change_quantile: Fraction
    thresholds_fit_on: Literal["training_fold_only"]
    freeze_thresholds_in_validation_and_test: bool
    absorption: Absorption
    event_cooldown_seconds: PositiveInt
    allow_unknown: bool

    @field_validator("freeze_thresholds_in_validation_and_test")
    @classmethod
    def freeze_enabled_state_thresholds(cls, value: bool, info: ValidationInfo) -> bool:
        # A disabled optional state module has no fitted thresholds to freeze.
        return require_true(value) if info.data.get("enabled") is True else value


class Split(Model):
    method: Literal["walk_forward"]
    train_days: PositiveInt
    validation_days: PositiveInt
    test_days: PositiveInt
    step_days: PositiveInt
    purge_by_actual_label_intervals: RequiredTrue
    minimum_boundary_buffer_seconds: PositiveInt
    shuffle: bool

    @field_validator("shuffle")
    @classmethod
    def chronological(cls, value: bool) -> bool:
        if value:
            raise ValueError("shuffle must be false for chronological research")
        return value


class Inference(Model):
    confidence_level: Fraction
    block_bootstrap_min_block_seconds: PositiveInt
    bootstrap_repetitions: PositiveInt
    multiple_testing_method: Literal["holm"]
    alpha: Fraction
    random_seed: NonNegativeInt


class Research(Model):
    horizon_seconds: Windows
    label_start: Literal["feature_or_state_actual_available_time"]
    return_definition: Literal["log_return_bps"]
    quote_alignment: Literal["backward_asof_with_max_age"]
    mfe_mae_grid_seconds: PositiveInt
    report_all_horizons: RequiredTrue
    report_common_event_sample: RequiredTrue
    split: Split
    inference: Inference

    @field_validator("horizon_seconds")
    @classmethod
    def unique_horizons(cls, values: list[int]) -> list[int]:
        return unique_positive(values)


class CostAnalysis(Model):
    enabled: bool
    position_quantity_btc: ExactPositive | None
    entry_fee_rate: ExactNonNegative | None
    exit_fee_rate: ExactNonNegative | None
    entry_latency_ms: NonNegativeInt | None
    exit_latency_ms: NonNegativeInt | None
    extra_slippage_bps: ExactNonNegative | None
    execution_model: Literal["taker_quote_or_validated_l2"]
    funding_model: Literal["actual_crossed_settlement_events"]
    missing_cost_policy: Literal["block_net_result"]

    @model_validator(mode="after")
    def complete_costs(self) -> Self:
        required = (
            "position_quantity_btc",
            "entry_fee_rate",
            "exit_fee_rate",
            "entry_latency_ms",
            "exit_latency_ms",
            "extra_slippage_bps",
        )
        missing = [key for key in required if getattr(self, key) is None]
        if self.enabled and missing:
            raise ValueError("enabled cost analysis requires: " + ", ".join(missing))
        return self


class AcceptanceTargets(Model):
    continuous_run_hours: PositiveInt
    core_valid_coverage_fraction: Coverage
    feature_publish_lag_p99_ms: PositiveInt
    unflagged_known_gaps: NonNegativeInt
    status: Literal["NOT_RUN", "PASS", "FAIL", "BLOCKED", "PARTIAL"]


class ResearchConfig(Model):
    spec_version: Literal["0.1"]
    project: Project
    instruments: Instruments
    endpoints: Endpoints
    streams: Streams
    collector: Collector
    storage: Storage
    features: Features
    quality: Quality
    states: States
    research: Research
    cost_analysis: CostAnalysis
    acceptance_targets: AcceptanceTargets
