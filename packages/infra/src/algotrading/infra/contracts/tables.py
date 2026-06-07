"""The typed table contracts — the only objects allowed across a seam.

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
    forward_price: float
    diagnostics: ForwardDiagnostics
    source_snapshot_ts: datetime
    provenance: ProvenanceStamp


@dataclass(frozen=True, slots=True)
class IvPoint:
    """A solved implied-volatility point for one option contract."""

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
    """Model price, Greeks, and monetized Greeks for one contract.

    The raw per-unit Greeks (``delta``…``rho``) are the source of truth; the
    ``dollar_*`` fields are a derived view, each quoted in the explicit unit pinned
    by ADR 0036 (Delta\\$ per \\$1 of underlying, Gamma\\$ per 1% move, Vega\\$ per
    1 vol point, Theta\\$ per calendar day, Rho\\$ per 1% rate). ``dollar_theta`` and
    ``dollar_rho`` are additive-nullable (``float | None``): they were added after the
    first three, so a partition written before they existed reads back with them
    ``None`` rather than failing the schema-evolution check (ADR 0036 / ADR 0029).
    """

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


@dataclass(frozen=True, slots=True)
class DailyBar:
    """One day's OHLC bar for an underlying — the price-history product (roadmap WS 1E).

    The underlying daily price history (index + every constituent) that powers the
    candlestick chart. It is a distinct product from the option
    :class:`MarketStateSnapshot`: full OHLC + volume so a candlestick is free, keyed by
    ``(provider, underlying, trade_date)`` — the ADR 0034 §4 partition tuple — so row
    identity is declared, not implied, and two sources of the same symbol never collide.

    Stored one-immutable-raw (ADR 0019); provider-partitioned (ADR 0017 / 0034 §4); the
    fetch that fills it is owned by 1C (CP REST ``/iserver/marketdata/history``, ADR 0031),
    not this contract. ``provider`` is the source label (``IBKR``, ``SAXO``…); ``bar_type``
    names the bar family (e.g. ``"1d-TRADES"``); ``source`` is a free-form lineage label.
    """

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
    """One bitemporal membership fact: a constituent's interval inside an index (WS 1A).

    This is point-in-time **reference data** describing the index, not a per-broker quote
    stream, so it is **provider-agnostic** — it carries no storage ``provider`` partition
    segment (ADR 0034 §4/§5). The data source is recorded as the ``vendor`` *field* (the
    OQ-3 source is Siblis Research), which is a different concept from the physical
    ``provider`` segment market-data tables carry: two vendors restating the same index
    are still the same index, so they must share a partition and be told apart by the
    knowledge axis, not split onto disjoint disk paths.

    **Bitemporal.** Two time axes are kept so a vendor restatement never rewrites history:

    * the *effective* axis — ``[effective_add_date, effective_remove_date)`` — the
      half-open interval during which the name was actually a member of the index. The
      interval is **half-open**: a name is in the basket on its ``effective_add_date`` and
      out on its ``effective_remove_date`` (the resolver pins this; see ``members``).
      ``effective_remove_date`` is ``None`` for a current, never-removed member (an
      open-ended interval).
    * the *knowledge* axis — ``knowledge_date`` — the date this membership fact was
      recorded / the vendor snapshot it came from. A later snapshot that restates a past
      membership writes a **new row** with a later ``knowledge_date``, never an in-place
      edit (ADR 0019/0034 immutability). "What did we believe on date X" is answered by
      filtering to ``knowledge_date <= X`` before the effective-axis join.

    ``weight`` is the as-of index weight and is **nullable**: where the source does not
    provide full weights it is recorded ``None`` (labeled unavailable), never silently
    zeroed or equal-weighted — a silent default would be an economic-correctness bug.

    Stored append-only in a ``reference`` layer, partitioned by index then
    ``effective_add_date`` (ADR 0034 §4/§5), and resolved by a DuckDB ``ASOF JOIN``
    (ADR 0033). The primary key ``(index, constituent, effective_add_date,
    knowledge_date)`` makes a restatement a new append-only row rather than a collision.
    """

    index: str
    constituent: str
    effective_add_date: date
    effective_remove_date: date | None
    knowledge_date: date
    vendor: str
    weight: float | None = None


@dataclass(frozen=True, slots=True)
class ProjectedOptionAnalytics:
    """One cell of the pinned tenor × delta-band analytics grid (WS 1F).

    For one underlying at one daily snapshot this is the deterministic projection of the
    fitted vol surface onto the **pinned tenor set** (``10d…3y``) crossed with a **delta
    band** (the 30Δ-put → ATM → 30Δ-call window). Each cell carries the fitted IV, the
    model price, and the full Greeks in **both** representations side by side — the raw
    decimal per-unit Greeks (``delta``…``rho``, the source of truth) and the derived
    dollar Greeks (``dollar_*``), each dollar number paired with an explicit ``*_unit``
    string so the row is self-describing and the BFF/front (1I) renders the unit without
    re-deriving it (OQ-1 / P0.2, ADR 0036).

    The grid axes are config (P0.1 tenor grid, the delta-band axis), the $-convention
    forks (gamma per 1% vs $1, theta ÷365 vs ÷252) come from validated config, and all of
    these enter the provenance ``config_hashes`` so the grid is reproducible.

    ``target_delta`` is the signed delta the band point was solved for (a put is negative,
    a call positive); ``delta`` is the option's *actual* decimal delta at the solved
    strike (they coincide up to the solver tolerance and the convention). The delta is in
    the **spot-delta** convention of ``pricing/black76.py`` — the strike is inverted
    against that same convention so the band lands on the right strikes (the spec gotcha).

    Provider-partitioned (ADR 0017 / 0034 §4): two sources of the same ``(underlying,
    trade_date)`` land in disjoint partitions. ``dollar_theta``/``dollar_rho`` and their
    unit strings are additive-nullable (``| None``) for the same schema-evolution reason
    as :class:`PricingResult`.
    """

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
    scenario_pnl: float
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
    qc_status: str
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
