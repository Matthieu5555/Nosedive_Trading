from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping
from types import MappingProxyType
from typing import Annotated, Any, Literal, NoReturn

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from ..hashing import canonical_dumps, sha256_hex


class ConfigFieldError(Exception):

    def __init__(self, section: str, field: str, value: Any, reason: str = "") -> None:
        self.section = section
        self.field = field
        self.value = value
        self.reason = reason
        suffix = f": {reason}" if reason else ""
        super().__init__(f"config {section}.{field} = {value!r} is invalid{suffix}")


_SECTION_CONFIG = ConfigDict(frozen=True, extra="forbid", strict=True)


def _raise_config_field_error(section: str, exc: ValidationError) -> NoReturn:
    error = exc.errors()[0]
    location = error.get("loc", ())
    field = ".".join(str(part) for part in location) if location else "<root>"
    raise ConfigFieldError(section, field, error.get("input"), error.get("msg", "")) from exc


class _ConfigModel(BaseModel):

    model_config = _SECTION_CONFIG

    def __init__(self, **data: Any) -> None:
        try:
            super().__init__(**data)
        except ValidationError as exc:
            _raise_config_field_error(type(self).__name__, exc)


def _list_to_tuple(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(value)
    return value


_FloatTuple = Annotated[tuple[float, ...], BeforeValidator(_list_to_tuple)]
_IntTuple = Annotated[tuple[int, ...], BeforeValidator(_list_to_tuple)]
_StrTuple = Annotated[tuple[str, ...], BeforeValidator(_list_to_tuple)]
_FloatPair = Annotated[tuple[float, ...], BeforeValidator(_list_to_tuple)]


DELTA_CONVENTIONS = ("forward_undiscounted", "spot_discounted")


class StrikeSelectionConfig(_ConfigModel):

    model_config = _SECTION_CONFIG

    version: str = Field(min_length=1)
    delta_bound: float = Field(default=0.30, gt=0.0, lt=1.0)
    delta_convention: Literal["forward_undiscounted", "spot_discounted"] = "forward_undiscounted"
    min_strikes_per_side: int = Field(default=2, ge=1)
    discovery_working_vol: float = Field(default=0.40, gt=0.0)
    discovery_pool_size: int = Field(default=6, ge=1)
    strike_window_pct: float = Field(default=0.35, gt=0.0, le=1.0)


class SignalEntryConfig(_ConfigModel):

    model_config = _SECTION_CONFIG

    version: str = Field(min_length=1)
    reference_tenor: str = Field(default="3m", min_length=1)
    term_slope_front: str = Field(default="1m", min_length=1)
    term_slope_back: str = Field(default="6m", min_length=1)
    iv_history_lookback_days: int = Field(default=365, gt=0)
    realized_vol_lookback_days: int = Field(default=30, gt=0)
    periods_per_year: float = Field(default=252.0, gt=0.0)
    basket_size: int | None = Field(default=None)

    @model_validator(mode="after")
    def _check_pillars_and_basket(self) -> SignalEntryConfig:
        if self.term_slope_front == self.term_slope_back:
            raise ValueError(
                "term_slope_front and term_slope_back must differ (the slope spans two pillars)"
            )
        if self.basket_size is not None and self.basket_size < 1:
            raise ValueError("basket_size must be >= 1 when set (or null for the full basket)")
        return self


class UniverseConfig(_ConfigModel):

    model_config = _SECTION_CONFIG

    version: str = Field(min_length=1)
    exchange: str = Field(min_length=1)
    tenor_grid: _StrTuple = ("10d", "1m", "3m", "6m", "12m", "18m", "2y", "3y")
    dispersion_top_n: int = Field(default=10, ge=1)
    indices: Mapping[str, Any] = Field(default_factory=dict)
    strike_selection: StrikeSelectionConfig = Field(
        default_factory=lambda: StrikeSelectionConfig(version="strike-selection-default")
    )
    signals: SignalEntryConfig = Field(
        default_factory=lambda: SignalEntryConfig(version="signals-default")
    )

    @model_validator(mode="after")
    def _check_tenor_grid_and_freeze_indices(self) -> UniverseConfig:
        if not self.tenor_grid:
            raise ValueError("tenor_grid must be non-empty")
        if len(set(self.tenor_grid)) != len(self.tenor_grid):
            raise ValueError("tenor_grid tenors must be unique")
        object.__setattr__(self, "indices", _canonical_indices(self.indices))
        return self


class GridQcConfig(_ConfigModel):

    model_config = _SECTION_CONFIG

    version: str = Field(min_length=1)
    tenor_floors: dict[str, Annotated[int, Field(ge=0)]] = Field(
        default_factory=lambda: {
            "10d": 5,
            "1m": 5,
            "3m": 5,
            "6m": 5,
            "12m": 5,
            "18m": 5,
            "2y": 5,
            "3y": 5,
        }
    )
    band_low_delta: float = Field(default=-0.30, ge=-1.0)
    band_high_delta: float = Field(default=0.30, le=1.0)
    band_step: float = Field(default=0.02, gt=0.0)
    max_delta_step: float = Field(default=0.25, gt=0.0)
    # Coverage is a ratio over the MONITORED (liquid) range, not a flat per-tenor floor
    # (ADR 0052 / blueprint 14-slos): the share of interior pinned tenors that are covered
    # (direct capture OR Eq.-22 interpolatable from liquid neighbours) must clear this.
    monitored_coverage_ratio: float = Field(default=0.95, ge=0.0, le=1.0)
    # Calendar (Eq. 21) pages CRITICAL only on a MATERIAL/GROSS variance inversion. A breach
    # is sub-threshold noise unless it exceeds BOTH an absolute total-variance gap and a gap
    # relative to the long-leg variance — and the short leg sits at/above the ultra-short floor.
    calendar_abs_variance_tol: float = Field(default=5e-4, ge=0.0)
    calendar_rel_variance_tol: float = Field(default=0.05, ge=0.0)
    # Maturities below this (years) are ultra-short: their variances are numerically noisy, so
    # a calendar inversion involving them is at most a WARNING (blueprint 05-math-notes).
    ultra_short_maturity_years: float = Field(default=14.0 / 365.0, ge=0.0)

    @model_validator(mode="after")
    def _check_band(self) -> GridQcConfig:
        if not self.band_low_delta < self.band_high_delta:
            raise ValueError("require band_low_delta < band_high_delta")
        if self.band_step > self.band_high_delta - self.band_low_delta:
            raise ValueError("band_step must be no wider than the band it samples")
        return self

    def floor_for(self, tenor: str) -> int:
        if tenor not in self.tenor_floors:
            raise ConfigFieldError(
                "grid_qc", "tenor_floors", tenor, "no coverage floor configured for pinned tenor"
            )
        return self.tenor_floors[tenor]


class ContinuityQcConfig(_ConfigModel):

    model_config = _SECTION_CONFIG

    version: str = Field(min_length=1)
    max_gap_count: int = Field(default=5, ge=0)
    warn_gap_count: int = Field(default=1, ge=0)
    min_coverage_ratio: float = Field(default=0.95, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _check_gap_bands(self) -> ContinuityQcConfig:
        if self.warn_gap_count > self.max_gap_count:
            raise ValueError("require warn_gap_count <= max_gap_count")
        return self


class ForwardEngineQcConfig(_ConfigModel):

    model_config = _SECTION_CONFIG

    version: str = Field(min_length=1)
    max_rel_residual_mad: float = Field(default=0.01, gt=0.0)
    min_forward_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    max_rel_parity_residual: float = Field(default=0.02, gt=0.0)


class FitToleranceQcConfig(_ConfigModel):

    model_config = _SECTION_CONFIG

    version: str = Field(min_length=1)
    max_non_convergence_ratio: float = Field(default=0.10, ge=0.0, le=1.0)
    max_surface_rmse: float = Field(default=0.02, gt=0.0)


class AnomalyQcConfig(_ConfigModel):

    model_config = _SECTION_CONFIG

    version: str = Field(min_length=1)
    mad_multiplier: float = Field(default=5.0, gt=0.0)
    warn_z: float = Field(default=3.5, gt=0.0)
    fail_z: float = Field(default=5.0, gt=0.0)
    min_baseline: int = Field(default=10, ge=1)

    @model_validator(mode="after")
    def _check_bands(self) -> AnomalyQcConfig:
        if self.fail_z < self.warn_z:
            raise ValueError("require fail_z >= warn_z")
        return self


class QuoteIntegrityQcConfig(_ConfigModel):

    model_config = _SECTION_CONFIG

    version: str = Field(min_length=1)
    min_two_sided_fraction: float = Field(default=0.10, ge=0.0, le=1.0)


class QcThresholdConfig(_ConfigModel):

    model_config = _SECTION_CONFIG

    version: str = Field(min_length=1)
    max_spread_pct: float = Field(gt=0.0)
    max_quote_age_seconds: float = Field(gt=0.0)
    min_chain_count: int = Field(ge=1)
    grid: GridQcConfig = Field(default_factory=lambda: GridQcConfig(version="grid-qc-default"))
    continuity: ContinuityQcConfig = Field(
        default_factory=lambda: ContinuityQcConfig(version="continuity-qc-default")
    )
    forward_engine: ForwardEngineQcConfig = Field(
        default_factory=lambda: ForwardEngineQcConfig(version="forward-engine-qc-default")
    )
    fit_tolerance: FitToleranceQcConfig = Field(
        default_factory=lambda: FitToleranceQcConfig(version="fit-tolerance-qc-default")
    )
    anomaly: AnomalyQcConfig = Field(
        default_factory=lambda: AnomalyQcConfig(version="anomaly-qc-default")
    )
    quote_integrity: QuoteIntegrityQcConfig = Field(
        default_factory=lambda: QuoteIntegrityQcConfig(version="quote-integrity-qc-default")
    )


class SolverConfig(_ConfigModel):

    model_config = _SECTION_CONFIG

    version: str = Field(min_length=1)
    iv_tolerance: float = Field(gt=0.0)
    max_iterations: int = Field(ge=1)
    vol_min: float = Field(default=1e-9, gt=0.0)
    vol_max: float = Field(default=5.0, gt=0.0)

    @model_validator(mode="after")
    def _check_bracket(self) -> SolverConfig:
        if not self.vol_min < self.vol_max:
            raise ValueError("need vol_min < vol_max")
        return self


_SURFACE_MODELS = frozenset({"svi"})
_SURFACE_FALLBACK_MODELS = frozenset({"nonparametric"})


class SurfaceConfig(_ConfigModel):

    model_config = _SECTION_CONFIG

    version: str = Field(min_length=1)
    svi_a_bounds: _FloatPair
    svi_b_bounds: _FloatPair
    svi_rho_bounds: _FloatPair
    svi_m_bounds: _FloatPair
    svi_sigma_bounds: _FloatPair
    svi_bound_hit_tol: float = Field(gt=0.0)
    svi_max_iterations: int = Field(ge=1)
    model: str = "svi"
    fallback_model: str = "nonparametric"
    min_points_per_slice: int = Field(default=5, ge=5)
    reroute_railed_dense_slice: bool = False
    reroute_min_points: int | None = Field(default=None, ge=5)
    moneyness_buckets: _FloatTuple = (-0.2, -0.1, 0.0, 0.1, 0.2)

    @property
    def reroute_point_floor(self) -> int:
        """Dense-enough threshold for the railed-slice reroute (defaults to the SVI-trust floor)."""
        if self.reroute_min_points is None:
            return self.min_points_per_slice
        return self.reroute_min_points

    @model_validator(mode="after")
    def _check_bound_pairs(self) -> SurfaceConfig:
        for name in (
            "svi_a_bounds",
            "svi_b_bounds",
            "svi_rho_bounds",
            "svi_m_bounds",
            "svi_sigma_bounds",
        ):
            pair = getattr(self, name)
            if len(pair) != 2:
                raise ValueError(f"{name} must be a (low, high) pair")
            low, high = pair
            if not low < high:
                raise ValueError(f"{name} need low < high")
        return self

    @model_validator(mode="after")
    def _check_moneyness_buckets(self) -> SurfaceConfig:
        buckets = self.moneyness_buckets
        if not buckets:
            raise ValueError("moneyness_buckets must be non-empty")
        if list(buckets) != sorted(buckets) or len(set(buckets)) != len(buckets):
            raise ValueError("moneyness_buckets must be strictly increasing")
        if 0.0 not in buckets:
            raise ValueError("moneyness_buckets must include 0.0 (the ATM/forward point)")
        if tuple(sorted(-k for k in buckets)) != tuple(buckets):
            raise ValueError("moneyness_buckets must be symmetric about 0.0")
        return self

    @model_validator(mode="after")
    def _check_models(self) -> SurfaceConfig:
        if self.model not in _SURFACE_MODELS:
            raise ValueError(
                f"model must be one of {sorted(_SURFACE_MODELS)} (the implemented fits), "
                f"got {self.model!r}"
            )
        if self.fallback_model not in _SURFACE_FALLBACK_MODELS:
            raise ValueError(
                f"fallback_model must be one of {sorted(_SURFACE_FALLBACK_MODELS)} "
                f"(the implemented fallbacks), got {self.fallback_model!r}"
            )
        return self


_FORWARD_OUTLIER_METHODS = frozenset({"mad", "none"})


class ForwardConfig(_ConfigModel):

    model_config = _SECTION_CONFIG

    version: str = Field(min_length=1)
    good_rel_residual: float = Field(gt=0.0)
    fair_rel_residual: float = Field(gt=0.0)
    full_credit_pairs: float = Field(gt=0.0)
    rel_residual_halflife: float = Field(gt=0.0)
    single_pair_confidence: float = Field(ge=0.0, le=1.0)
    rate: float | None = None
    max_candidate_count: int | None = None
    outlier_method: str = "mad"
    max_robust_zscore: float = Field(default=3.5, gt=0.0)

    @model_validator(mode="after")
    def _check_forward_engine(self) -> ForwardConfig:
        if self.outlier_method not in _FORWARD_OUTLIER_METHODS:
            raise ValueError(
                f"outlier_method must be one of {sorted(_FORWARD_OUTLIER_METHODS)}, "
                f"got {self.outlier_method!r}"
            )
        if self.max_candidate_count is not None and self.max_candidate_count < 2:
            raise ValueError(
                "max_candidate_count must be >= 2 (the parity regression needs two pairs) "
                f"or None for no cap, got {self.max_candidate_count}"
            )
        return self


_RATE_DAY_COUNTS = ("ACT/365", "ACT/360")
_RATE_COMPOUNDINGS = ("continuous", "simple")
_RATE_INTERPOLATIONS = ("linear_zero",)
_RATE_QC_DISPOSITIONS = ("warn", "fail")


class RatePillarConfig(_ConfigModel):
    """One pillar of a per-currency risk-free curve (ADR 0054).

    A pillar names the published instrument it is sourced from (`instrument`, e.g. `estr_on`,
    `euribor_3m`, `ois_2y` — a config label, never economics) and the pinned tenor it sits at,
    expressed as a continuous-ACT/365 `maturity_years` so the curve evaluator interpolates in the
    same year-fraction the option `maturity_years` uses.
    """

    model_config = _SECTION_CONFIG

    tenor_label: str = Field(min_length=1)
    maturity_years: float = Field(gt=0.0)
    instrument: str = Field(min_length=1)


class CurrencyRateConfig(_ConfigModel):
    """The risk-free curve definition for one currency (ADR 0054, RULED 1–5).

    Names the source, the pillar set, the day-count + compounding the source publishes (converted
    to the canonical continuous/ACT-365 on ingest), the between-pillar interpolation convention, and
    the warn-only implied−riskfree spread-QC bound. Every value is typed config, never a literal.
    """

    model_config = _SECTION_CONFIG

    currency: str = Field(min_length=1)
    source: str = Field(min_length=1)
    day_count: Literal["ACT/365", "ACT/360"] = "ACT/365"
    compounding: Literal["continuous", "simple"] = "continuous"
    interpolation: Literal["linear_zero"] = "linear_zero"
    pillars: Annotated[tuple[RatePillarConfig, ...], BeforeValidator(_list_to_tuple)] = ()
    spread_qc_abs_bound: float = Field(default=0.02, ge=0.0)
    spread_qc_disposition: Literal["warn", "fail"] = "warn"

    @model_validator(mode="after")
    def _check_pillars(self) -> CurrencyRateConfig:
        if not self.pillars:
            raise ValueError(
                f"currency {self.currency!r} must declare at least one pillar (the degenerate "
                "flat curve is a single pillar)"
            )
        tenors = [p.maturity_years for p in self.pillars]
        if any(b <= a for a, b in zip(tenors, tenors[1:], strict=False)):
            raise ValueError(
                f"currency {self.currency!r} pillars must be in strictly increasing maturity order"
            )
        labels = [p.tenor_label for p in self.pillars]
        if len(set(labels)) != len(labels):
            raise ValueError(f"currency {self.currency!r} pillar tenor_labels must be unique")
        return self


class RatesConfig(_ConfigModel):
    """Per-currency risk-free rate-curve config (ADR 0054 / R1).

    The typed-config home of the ingested external `r(T)` curve: a `currency -> CurrencyRateConfig`
    map. Lives in its OWN `config_hashes["rates"]` bundle so adding it leaves the `pricing`/`qc`/
    `scenarios`/`universe` bundle hashes byte-identical (no forward/analytics golden moves on the
    rate curve's account; the canonical `ForwardConfig.rate: null` path stays parity-implied).
    """

    model_config = _SECTION_CONFIG

    version: str = Field(min_length=1)
    currencies: Mapping[str, CurrencyRateConfig] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_currencies(self) -> RatesConfig:
        for code, cfg in self.currencies.items():
            if cfg.currency != code:
                raise ValueError(
                    f"rates currency key {code!r} must match its currency field {cfg.currency!r}"
                )
        object.__setattr__(self, "currencies", MappingProxyType(dict(self.currencies)))
        return self

    def for_currency(self, currency: str) -> CurrencyRateConfig:
        try:
            return self.currencies[currency]
        except KeyError:
            raise ConfigFieldError(
                "rates", "currencies", currency, "no risk-free curve configured for currency"
            ) from None


class StressSurfaceConfig(_ConfigModel):

    model_config = _SECTION_CONFIG

    version: str = Field(min_length=1)
    spot_shock_abs: float = Field(default=0.10, ge=0.0)
    vol_shock_abs: float = Field(default=0.10, ge=0.0)
    spot_steps: int = Field(default=3, ge=1)
    vol_steps: int = Field(default=3, ge=1)

    @model_validator(mode="after")
    def _check_steps_odd(self) -> StressSurfaceConfig:
        for name in ("spot_steps", "vol_steps"):
            steps = getattr(self, name)
            if steps % 2 == 0:
                raise ValueError(f"{name} must be odd so the centre (0 shock) cell is sampled")
        return self


class NamedScenarioConfig(_ConfigModel):

    model_config = _SECTION_CONFIG

    label: str = Field(min_length=1)
    spot_shock: float = 0.0
    vol_shock: float = 0.0
    rate_shock: float = 0.0
    correlation_shock: float = 0.0


_NamedScenarioTuple = Annotated[
    tuple[NamedScenarioConfig, ...], BeforeValidator(_list_to_tuple)
]


class ScenarioConfig(_ConfigModel):

    model_config = _SECTION_CONFIG

    version: str = Field(min_length=1)
    spot_shocks: _FloatTuple
    vol_shocks: _FloatTuple
    rate_shocks: _FloatTuple = ()
    correlation_shocks: _FloatTuple = ()
    named_scenarios: _NamedScenarioTuple = ()
    roll_down_days: _IntTuple = (1,)
    stress_surface: StressSurfaceConfig = Field(
        default_factory=lambda: StressSurfaceConfig(version="stress-surface-default")
    )

    @model_validator(mode="after")
    def _check_roll_down_days(self) -> ScenarioConfig:
        for days in self.roll_down_days:
            if days <= 0:
                raise ValueError("roll_down_days must be a positive day count")
        return self

    @model_validator(mode="after")
    def _check_named_scenario_labels(self) -> ScenarioConfig:
        labels = [named.label for named in self.named_scenarios]
        if len(set(labels)) != len(labels):
            raise ValueError(
                f"named_scenarios labels must be unique, got {labels}"
            )
        return self


GAMMA_NORMALISATIONS = ("one_pct", "one_dollar")
THETA_DAY_COUNTS = (365, 252)


class MonetizationConfig(_ConfigModel):

    model_config = _SECTION_CONFIG

    version: str = Field(min_length=1)
    gamma_normalisation: Literal["one_pct", "one_dollar"] = "one_pct"
    theta_day_count: Literal[365, 252] = 365


class PlatformConfig(_ConfigModel):

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    universe: UniverseConfig
    qc_threshold: QcThresholdConfig
    solver: SolverConfig
    surface: SurfaceConfig
    forward: ForwardConfig
    scenario: ScenarioConfig
    monetization: MonetizationConfig = Field(
        default_factory=lambda: MonetizationConfig(version="monetization-default")
    )
    rates: RatesConfig = Field(
        default_factory=lambda: RatesConfig(version="rates-default")
    )


SECTION_NAMES = (
    "universe",
    "qc_threshold",
    "solver",
    "surface",
    "forward",
    "scenario",
    "monetization",
    "rates",
)


def _canonical_indices(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(k): _canonical_indices(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_canonical_indices(v) for v in value)
    return value


def _canonical(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return {name: _canonical(getattr(value, name)) for name in type(value).model_fields}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _canonical(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, Mapping):
        return {str(k): _canonical(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    if isinstance(value, float):
        return 0.0 if value == 0.0 else value
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(_canonical(value), sort_keys=True, separators=(",", ":"), allow_nan=False)


def object_config_hash(value: Any) -> str:
    return sha256_hex(canonical_json(value))


def config_hash(config: PlatformConfig) -> str:
    return object_config_hash(config)


def section_hash(config: PlatformConfig, section: str) -> str:
    if section not in SECTION_NAMES:
        raise KeyError(section)
    return object_config_hash(getattr(config, section))


def section_versions(config: PlatformConfig) -> dict[str, str]:
    return {name: getattr(config, name).version for name in SECTION_NAMES}


_BUNDLE_SECTIONS: dict[str, tuple[str, ...]] = {
    "universe": ("universe",),
    "qc": ("qc_threshold",),
    "pricing": ("solver", "surface", "forward"),
    "scenarios": ("scenario", "monetization"),
    "rates": ("rates",),
}


def config_snapshot(config: PlatformConfig) -> dict[str, Any]:
    return {name: _canonical(getattr(config, name)) for name in SECTION_NAMES}


def config_hashes(config: PlatformConfig) -> dict[str, str]:
    return {
        bundle: object_config_hash({name: getattr(config, name) for name in names})
        for bundle, names in _BUNDLE_SECTIONS.items()
    }


def composite_config_hash(parts: Mapping[str, str]) -> str:
    return sha256_hex(canonical_dumps({str(k): str(v) for k, v in parts.items()}))
