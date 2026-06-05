"""The twelve typed table contracts — the only objects allowed across a seam.

Each dataclass is one table family from the roadmap (Part IV.C), frozen so it is
immutable and compares by value (which is what makes write/read round-trips
checkable). Primary keys match the roadmap exactly. Numbers are ``float``/``int``,
never decimal-strings. Timestamps are timezone-aware. Every derived record carries
a :class:`ProvenanceStamp` and a ``source_snapshot_ts`` back-reference to the
snapshot it was computed from.

Maturity follows the house convention: ``maturity_years`` (a float) is the value
analytics use, but the original ``expiry_date`` and ``day_count`` are stored
beside it so the year-fraction can always be re-derived.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from algotrading.core.provenance import ProvenanceStamp

from .bundles import ForwardDiagnostics, IvDiagnostics, SurfaceFitDiagnostics
from .instrument_key import InstrumentKey


@dataclass(frozen=True, slots=True)
class InstrumentMaster:
    """Canonical record for one instrument as known on a given date.

    Composite key (instrument_key, as_of_date) makes this point-in-time: the same
    instrument can have different rows on different dates. The raw broker payload
    is kept verbatim (as a JSON string) as evidence for how the row was resolved.
    """

    instrument_key: str
    as_of_date: date
    instrument: InstrumentKey
    raw_broker_payload: str


@dataclass(frozen=True, slots=True)
class RawMarketEvent:
    """One immutable observation from the broker feed (append-only).

    Carries the three timestamps: ``exchange_ts`` (exchange's time), ``receipt_ts``
    (when we got it), ``canonical_ts`` (the time used for ordering and as-of reads).
    """

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
    """A time-aligned, quality-labeled view of one instrument at one instant.

    Derived from raw events, so it carries a provenance stamp pointing at them.
    It is itself the snapshot, so it has no ``source_snapshot_ts``.
    """

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


@dataclass(frozen=True, slots=True)
class ForwardCurvePoint:
    """The chosen forward for one underlying/maturity, plus its diagnostics."""

    snapshot_ts: datetime
    underlying: str
    maturity_years: float
    expiry_date: date
    day_count: str
    forward: float
    diagnostics: ForwardDiagnostics
    source_snapshot_ts: datetime
    provenance: ProvenanceStamp


@dataclass(frozen=True, slots=True)
class IvPoint:
    """A solved implied-volatility point for one option contract."""

    snapshot_ts: datetime
    contract_key: str
    iv: float
    k: float
    total_variance: float
    solver_version: str
    diagnostics: IvDiagnostics
    source_snapshot_ts: datetime
    provenance: ProvenanceStamp


@dataclass(frozen=True, slots=True)
class SurfaceParameters:
    """Fitted SVI parameters for one underlying/maturity slice."""

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
    """One regularized total-variance grid cell, for use by other services."""

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
    """Model price, Greeks, and monetized Greeks for one contract."""

    snapshot_ts: datetime
    contract_key: str
    pricer_version: str
    price: float
    delta: float
    gamma: float
    vega: float
    theta: float
    rho: float
    cash_delta: float
    cash_gamma: float
    cash_vega: float
    source_snapshot_ts: datetime
    provenance: ProvenanceStamp


@dataclass(frozen=True, slots=True)
class Position:
    """A source-of-record or hypothetical position. An input, not a derived value."""

    valuation_ts: datetime
    portfolio_id: str
    contract_key: str
    quantity: float
    source: str


@dataclass(frozen=True, slots=True)
class RiskAggregate:
    """Net sensitivities for one portfolio group at a valuation time."""

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
    """Stress PnL for one contract under one scenario shock."""

    valuation_ts: datetime
    portfolio_id: str
    scenario_id: str
    contract_key: str
    spot_shock: float
    vol_shock: float
    time_shock: float
    pnl: float
    scenario_version: str
    source_snapshot_ts: datetime
    provenance: ProvenanceStamp


@dataclass(frozen=True, slots=True)
class QcResult:
    """The outcome of one named quality check against one target.

    ``run_ts`` is the time the check ran; it places the result on the
    time-partitioned layout and makes QC results queryable by day.
    """

    run_id: str
    check_name: str
    target_key: str
    run_ts: datetime
    status: str
    severity: str
    measured_value: float
    threshold_version: str
    context: str


@dataclass(frozen=True, slots=True)
class TriageRecord:
    """One thing for an operator to investigate, from any quality plane.

    The single, persisted triage shape both quality planes collapse into: the named
    QC checks (``source="qc"``) and the validation/anomaly layer
    (``source="validation"``/``"anomaly"``). Holding both as one row means a day's
    whole triage list is one queryable table, ordered and escalated by one rule, with
    no second result shape for a reporting layer to reconcile.

    The specificity discipline is preserved across the merge: ``target_key`` names the
    exact offending object (a maturity, a quote, a solver, a metric) and ``detail`` is
    the one-line headline that names it, never a generic banner. ``reason_code`` is the
    machine-readable why; ``run_ts`` places the row on the time-partitioned layout.
    """

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
