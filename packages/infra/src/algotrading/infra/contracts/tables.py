from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime

from algotrading.core.provenance import ProvenanceStamp

from .bundles import (
    ForwardDiagnostics,
    IvDiagnostics,
    RatesDiagnostics,
    SurfaceFitDiagnostics,
)
from .errors import ContractValidationError
from .instrument_key import InstrumentKey


@dataclass(frozen=True, slots=True)
class InstrumentMaster:

    instrument_key: str
    as_of_date: date
    instrument: InstrumentKey
    raw_broker_payload: str


@dataclass(frozen=True, slots=True)
class RawMarketEvent:

    session_id: str
    event_id: str
    instrument_key: str
    exchange_ts: datetime
    receipt_ts: datetime
    canonical_ts: datetime
    field_name: str
    value: float
    trade_date: date
    underlying: str


@dataclass(frozen=True, slots=True)
class MarketStateSnapshot:

    snapshot_ts: datetime
    instrument_key: str
    reference_spot: float
    bid: float
    ask: float
    last: float
    spread_pct: float
    reference_type: str
    flags: tuple[str, ...]
    completeness: float
    trade_date: date
    underlying: str
    provenance: ProvenanceStamp
    volume: float | None = None


@dataclass(frozen=True, slots=True)
class ForwardCurvePoint:

    snapshot_ts: datetime
    underlying: str
    maturity_years: float
    expiry_date: date
    day_count: str
    forward_price: float
    diagnostics: ForwardDiagnostics
    source_snapshot_ts: datetime
    provenance: ProvenanceStamp
    implied_rate: float | None = None
    implied_carry: float | None = None
    implied_dividend: float | None = None


@dataclass(frozen=True, slots=True)
class RiskFreeRatePoint:
    """One pillar of an ingested per-currency risk-free curve, as-of dated (ADR 0054 / R1).

    Contract `rates(currency, pillar_tenor, rate, as_of)`. The canonical `rate` is **continuous
    compounding under ACT-365** (consistent with `maturity_years`); a source publishing simple or
    money-market (ACT/360) rates is converted on ingest (the source convention is recorded on
    `diagnostics`). `as_of` is the publication date — a reconstruction for past day D reads only the
    curve published as-of D (no look-ahead). `maturity_years` is the pillar's tenor as a year
    fraction so the curve evaluator interpolates in the same units an option's `maturity_years` uses.
    """

    as_of: date
    currency: str
    pillar_tenor: str
    maturity_years: float
    rate: float
    day_count: str
    diagnostics: RatesDiagnostics
    source_snapshot_ts: datetime
    provenance: ProvenanceStamp

    def __post_init__(self) -> None:
        table = "rates"
        if not self.currency.strip():
            raise ContractValidationError(table, "currency", self.currency, "must be non-empty")
        if not self.pillar_tenor.strip():
            raise ContractValidationError(
                table, "pillar_tenor", self.pillar_tenor, "must be non-empty"
            )
        if not (math.isfinite(self.maturity_years) and self.maturity_years > 0.0):
            raise ContractValidationError(
                table, "maturity_years", self.maturity_years, "must be a finite positive year fraction"
            )
        if not math.isfinite(self.rate):
            raise ContractValidationError(table, "rate", self.rate, "must be a finite number")


@dataclass(frozen=True, slots=True)
class IvPoint:

    snapshot_ts: datetime
    contract_key: str
    implied_vol: float
    log_moneyness: float
    total_variance: float
    solver_version: str
    diagnostics: IvDiagnostics
    source_snapshot_ts: datetime
    provenance: ProvenanceStamp


@dataclass(frozen=True, slots=True)
class SurfaceParameters:

    snapshot_ts: datetime
    underlying: str
    maturity_years: float
    model_version: str
    svi_a: float
    svi_b: float
    svi_rho: float
    svi_m: float
    svi_sigma: float
    expiry_date: date
    day_count: str
    diagnostics: SurfaceFitDiagnostics
    source_snapshot_ts: datetime
    provenance: ProvenanceStamp


@dataclass(frozen=True, slots=True)
class SurfaceGrid:

    snapshot_ts: datetime
    underlying: str
    maturity_years: float
    moneyness_bucket: float
    model_version: str
    total_variance: float
    source_snapshot_ts: datetime
    provenance: ProvenanceStamp


@dataclass(frozen=True, slots=True)
class PricingResult:

    snapshot_ts: datetime
    contract_key: str
    pricer_version: str
    price: float
    delta: float
    gamma: float
    vega: float
    theta: float
    rho: float
    dollar_delta: float
    dollar_gamma: float
    dollar_vega: float
    source_snapshot_ts: datetime
    provenance: ProvenanceStamp
    dollar_theta: float | None = None
    dollar_rho: float | None = None
    vanna: float | None = None
    volga: float | None = None
    charm: float | None = None
    dollar_vanna: float | None = None
    dollar_volga: float | None = None
    dollar_charm: float | None = None
    rt_vega: float | None = None
    dollar_rt_vega: float | None = None


@dataclass(frozen=True, slots=True)
class DailyBar:

    provider: str
    underlying: str
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    bar_type: str
    source: str
    provenance: ProvenanceStamp


@dataclass(frozen=True, slots=True)
class IndexConstituent:

    index: str
    constituent: str
    effective_add_date: date
    effective_remove_date: date | None
    knowledge_date: date
    vendor: str
    weight: float | None = None


SURFACE_SIDES = ("put", "call", "combined")
SURFACE_SIDE_COMBINED = "combined"


@dataclass(frozen=True, slots=True)
class ProjectedOptionAnalytics:

    snapshot_ts: datetime
    provider: str
    underlying: str
    tenor_label: str
    maturity_years: float
    delta_band: str
    target_delta: float
    log_moneyness: float
    strike: float
    forward_price: float
    implied_vol: float
    total_variance: float
    price: float
    delta: float
    gamma: float
    vega: float
    theta: float
    rho: float
    dollar_delta: float
    dollar_gamma: float
    dollar_vega: float
    dollar_delta_unit: str
    dollar_gamma_unit: str
    dollar_vega_unit: str
    model_version: str
    pricer_version: str
    source_snapshot_ts: datetime
    provenance: ProvenanceStamp
    dollar_theta: float | None = None
    dollar_rho: float | None = None
    dollar_theta_unit: str | None = None
    dollar_rho_unit: str | None = None
    rt_vega: float | None = None
    dollar_rt_vega: float | None = None
    dollar_rt_vega_unit: str | None = None
    surface_side: str = SURFACE_SIDE_COMBINED
    price_mirror: float | None = None
    delta_mirror: float | None = None
    theta_mirror: float | None = None
    rho_mirror: float | None = None
    dollar_delta_mirror: float | None = None
    dollar_theta_mirror: float | None = None
    dollar_rho_mirror: float | None = None
    vanna: float | None = None
    volga: float | None = None
    charm: float | None = None
    dollar_vanna: float | None = None
    dollar_volga: float | None = None
    dollar_charm: float | None = None
    dollar_vanna_unit: str | None = None
    dollar_volga_unit: str | None = None
    dollar_charm_unit: str | None = None

    def __post_init__(self) -> None:
        if self.surface_side not in SURFACE_SIDES:
            raise ContractValidationError(
                "projected_option_analytics", "surface_side", self.surface_side,
                f"must be one of {SURFACE_SIDES}",
            )


@dataclass(frozen=True, slots=True)
class Position:

    valuation_ts: datetime
    portfolio_id: str
    contract_key: str
    quantity: float
    source: str


@dataclass(frozen=True, slots=True)
class RiskAggregate:

    valuation_ts: datetime
    portfolio_id: str
    group_key: str
    net_delta: float
    net_gamma: float
    net_vega: float
    net_theta: float
    source_snapshot_ts: datetime
    provenance: ProvenanceStamp


@dataclass(frozen=True, slots=True)
class ScenarioResult:

    valuation_ts: datetime
    portfolio_id: str
    scenario_id: str
    contract_key: str
    spot_shock: float
    vol_shock: float
    time_shock: float
    scenario_pnl: float
    scenario_version: str
    source_snapshot_ts: datetime
    provenance: ProvenanceStamp
    rate_shock: float | None = None


@dataclass(frozen=True, slots=True)
class ScenarioAttribution:

    valuation_ts: datetime
    portfolio_id: str
    scenario_id: str
    contract_key: str
    level: str
    spot_shock: float
    vol_shock: float
    time_shock: float
    delta_pnl: float
    gamma_pnl: float
    vega_pnl: float
    theta_pnl: float
    approx_pnl: float
    full_reprice_pnl: float
    residual: float
    within_tolerance: bool
    residual_abs_tol: float
    residual_rel_tol: float
    scenario_version: str
    attribution_version: str
    source_snapshot_ts: datetime
    provenance: ProvenanceStamp
    rho_pnl: float | None = None
    vanna_pnl: float | None = None
    volga_pnl: float | None = None


RESIDUAL_LEVELS = ("book", "strategy")


@dataclass(frozen=True, slots=True)
class ResidualObservation:
    """One as-of row of the realized-attribution residual time series.

    The residual is the part of realized day-over-day P&L that the deterministic
    Taylor decomposition (delta..volga) cannot name. This record banks that residual
    per book/strategy alongside (a) the named terms it is the remainder of and
    (b) the candidate *unmodeled-exposure* covariates that were observable as-of
    ``as_of_date``. It is the raw material the residual-diagnosis regression
    consumes once enough days are banked.

    Every covariate is ``float | None``: an exposure that could not be observed
    as-of is recorded as ``None`` (honestly absent), never as a fabricated zero.
    """

    as_of_date: date
    portfolio_id: str
    underlying: str
    level: str
    # The realized day-over-day attribution this residual is the remainder of.
    realized_pnl: float
    approx_pnl: float
    residual: float
    # The named Taylor terms (the part the Greek model *can* name).
    delta_pnl: float
    gamma_pnl: float
    vega_pnl: float
    theta_pnl: float
    rho_pnl: float
    vanna_pnl: float
    volga_pnl: float
    attribution_version: str
    diagnosis_version: str
    source_snapshot_ts: datetime
    provenance: ProvenanceStamp
    # Candidate unmodeled-exposure covariates, observed as-of (None if unavailable).
    # Skew / vanna proxies from the per-side surfaces.
    skew_proxy: float | None = None
    vanna_proxy: float | None = None
    # Regime / vol-of-vol from the signal layer.
    regime_proxy: float | None = None
    vol_of_vol_proxy: float | None = None
    # Liquidity / execution-slippage proxies.
    liquidity_proxy: float | None = None
    slippage_proxy: float | None = None

    def __post_init__(self) -> None:
        if self.level not in RESIDUAL_LEVELS:
            raise ContractValidationError(
                "residual_observations", "level", self.level,
                f"must be one of {RESIDUAL_LEVELS}",
            )
        if not self.portfolio_id.strip():
            raise ContractValidationError(
                "residual_observations", "portfolio_id", self.portfolio_id, "must be non-empty",
            )


@dataclass(frozen=True, slots=True)
class BookGreeks:

    valuation_ts: datetime
    book_id: str
    level: str
    layer_label: str
    layer_index: int
    net_delta: float
    net_gamma: float
    net_vega: float
    net_theta: float
    dollar_delta: float
    dollar_gamma: float
    dollar_vega: float
    dollar_theta: float
    dollar_rho: float
    dollar_delta_unit: str
    dollar_gamma_unit: str
    dollar_vega_unit: str
    dollar_theta_unit: str
    dollar_rho_unit: str
    composition_version: str
    source_snapshot_ts: datetime
    provenance: ProvenanceStamp


@dataclass(frozen=True, slots=True)
class StrategySignal:

    snapshot_ts: datetime
    provider: str
    underlying: str
    signal_kind: str
    subject: str
    tenor_label: str
    value: float
    source_snapshot_ts: datetime
    provenance: ProvenanceStamp


@dataclass(frozen=True, slots=True)
class QcResult:

    run_id: str
    check_name: str
    target_key: str
    run_ts: datetime
    qc_status: str
    severity: str
    measured_value: float
    threshold_version: str
    context: str


@dataclass(frozen=True, slots=True)
class TriageRecord:

    run_id: str
    run_ts: datetime
    underlying: str
    source: str
    name: str
    target_key: str
    status: str
    severity: str
    reason_code: str
    detail: str
    threshold_version: str


INSTRUMENT_KINDS = ("option", "stock")
LEG_SIDES = ("long", "short")


@dataclass(frozen=True, slots=True)
class BasketLeg:

    instrument_kind: str
    side: str
    quantity: float
    underlying: str
    tenor_label: str | None = None
    delta_band: str | None = None
    surface_side: str = SURFACE_SIDE_COMBINED

    def __post_init__(self) -> None:
        table = "baskets"
        if self.instrument_kind not in INSTRUMENT_KINDS:
            raise ContractValidationError(
                table, "instrument_kind", self.instrument_kind,
                f"must be one of {INSTRUMENT_KINDS}",
            )
        if self.side not in LEG_SIDES:
            raise ContractValidationError(
                table, "side", self.side, f"must be one of {LEG_SIDES}",
            )
        if self.surface_side not in SURFACE_SIDES:
            raise ContractValidationError(
                table, "surface_side", self.surface_side, f"must be one of {SURFACE_SIDES}",
            )
        if not self.underlying.strip():
            raise ContractValidationError(
                table, "underlying", self.underlying, "must be non-empty",
            )
        if not math.isfinite(self.quantity):
            raise ContractValidationError(
                table, "quantity", self.quantity, "must be a finite number",
            )
        if self.quantity == 0:
            raise ContractValidationError(
                table, "quantity", self.quantity, "must be non-zero",
            )
        if self.side == "long" and self.quantity < 0:
            raise ContractValidationError(
                table, "quantity", self.quantity, "a long leg must have a positive quantity",
            )
        if self.side == "short" and self.quantity > 0:
            raise ContractValidationError(
                table, "quantity", self.quantity, "a short leg must have a negative quantity",
            )
        if self.instrument_kind == "option" and (
            self.tenor_label is None or self.delta_band is None
        ):
            raise ContractValidationError(
                table, "tenor_label", (self.tenor_label, self.delta_band),
                "an option leg must name its grid cell (tenor_label and delta_band)",
            )
        if self.instrument_kind == "stock" and (
            self.tenor_label is not None or self.delta_band is not None
        ):
            raise ContractValidationError(
                table, "tenor_label", (self.tenor_label, self.delta_band),
                "a stock leg has no tenor/band (both must be None)",
            )


@dataclass(frozen=True, slots=True)
class Basket:

    basket_id: str
    trade_date: date
    underlying: str
    legs: tuple[BasketLeg, ...]
    provider: str | None = None
    strategy_id: str | None = None

    def __post_init__(self) -> None:
        table = "baskets"
        if not self.basket_id.strip():
            raise ContractValidationError(
                table, "basket_id", self.basket_id, "must be non-empty",
            )
        if not self.underlying.strip():
            raise ContractValidationError(
                table, "underlying", self.underlying, "must be non-empty",
            )
        if self.strategy_id is not None and not self.strategy_id.strip():
            raise ContractValidationError(
                table, "strategy_id", self.strategy_id,
                "when present (the strategy-identity stamp) must be a non-empty string",
            )


FILL_SIDES = ("BUY", "SELL")


@dataclass(frozen=True, slots=True)
class BrokerPosition:

    as_of_ts: datetime
    account_id: str
    conid: int
    contract_key: str
    quantity: float
    avg_cost: float
    market_price: float
    market_value: float
    currency: str

    def __post_init__(self) -> None:
        if not self.account_id.strip():
            raise ContractValidationError(
                "broker_positions", "account_id", self.account_id, "must be non-empty",
            )
        if not self.currency.strip():
            raise ContractValidationError(
                "broker_positions", "currency", self.currency, "must be non-empty",
            )


@dataclass(frozen=True, slots=True)
class BrokerCashBalance:

    as_of_ts: datetime
    account_id: str
    currency: str
    cash_balance: float
    settled_cash: float
    net_liquidation: float

    def __post_init__(self) -> None:
        if not self.account_id.strip():
            raise ContractValidationError(
                "broker_cash_balances", "account_id", self.account_id, "must be non-empty",
            )
        if not self.currency.strip():
            raise ContractValidationError(
                "broker_cash_balances", "currency", self.currency, "must be non-empty",
            )


@dataclass(frozen=True, slots=True)
class BrokerFill:

    account_id: str
    execution_id: str
    conid: int
    contract_key: str
    side: str
    quantity: float
    price: float
    currency: str
    venue_ts: datetime
    trade_date: date

    def __post_init__(self) -> None:
        if not self.account_id.strip():
            raise ContractValidationError(
                "broker_fills", "account_id", self.account_id, "must be non-empty",
            )
        if not self.execution_id.strip():
            raise ContractValidationError(
                "broker_fills", "execution_id", self.execution_id, "must be non-empty",
            )
        if self.side not in FILL_SIDES:
            raise ContractValidationError(
                "broker_fills", "side", self.side, f"must be one of {FILL_SIDES}",
            )


@dataclass(frozen=True, slots=True)
class BrokerAccountSnapshot:

    account_id: str
    as_of_ts: datetime
    positions: tuple[BrokerPosition, ...]
    cash_balances: tuple[BrokerCashBalance, ...]
    fills: tuple[BrokerFill, ...]


@dataclass(frozen=True, slots=True)
class ConidEntry:

    month: str
    expiry: str
    strike: float
    right: str
    conid: str


@dataclass(frozen=True, slots=True)
class DiscoveryCacheRow:

    underlying: str
    as_of_date: date
    exchange: str
    multiplier: str
    months: tuple[str, ...]
    expirations: tuple[str, ...]
    strikes: tuple[float, ...]
    entries: tuple[ConidEntry, ...]
