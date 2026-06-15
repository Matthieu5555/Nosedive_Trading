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

import math
from dataclasses import dataclass
from datetime import date, datetime

from algotrading.core.provenance import ProvenanceStamp

from .bundles import ForwardDiagnostics, IvDiagnostics, SurfaceFitDiagnostics
from .errors import ContractValidationError
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

    The second-order set (``vanna``/``volga``/``charm``, raw, and their ``dollar_*``
    monetizations — TARGET §7.2) is carried in the *same* dual representation and is
    additive-nullable for the same schema-evolution reason: a partition written before
    this lane reads them back ``None``, the closed-form Black-76 path fills them. Their
    unit strings are not stored here (the BFF looks them up in
    :data:`~algotrading.infra.pricing.dollar_greeks.UNIT_STRINGS`, exactly as it does
    for the first-order dollar Greeks — Vanna\\$/Volga\\$ per 1 vol point, Charm\\$ per
    calendar day). Charm is emitted for risk display only; it is *not* one of the
    P&L-attribution terms (those are Δ/Γ/Vega/Θ/Rho/Vanna/Volga — TARGET §2.5).

    ``rt_vega`` (running-time / annualised vega, ADR 0050) and its ``dollar_rt_vega`` ride
    the same dual representation and the same additive-nullable schema-evolution rule:
    ``rt_vega = vega / sqrt(T)`` strips the maturity factor so vega is comparable across
    strikes/tenors; the dollar form is per 1 vol point like Vega\\$ (its unit string is
    looked up in :data:`~algotrading.infra.pricing.dollar_greeks.UNIT_STRINGS`, not stored).
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


# Which fitted vol surface a grid cell's IV was read from (ADR 0048, R2). ``combined`` is the
# legacy single surface (fit over both rights) and the forward-backing / attribution reference;
# ``put``/``call`` are the per-side fits used by the put−call IV spread and wing-aware strategies.
SURFACE_SIDES = ("put", "call", "combined")
SURFACE_SIDE_COMBINED = "combined"


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
    as :class:`PricingResult`. ``rt_vega``/``dollar_rt_vega`` (running-time / annualised
    vega ``vega/sqrt(T)``, ADR 0050) are carried per strike in the same dual representation,
    additive-nullable for the same reason; ``dollar_rt_vega_unit`` is fixed (``"$ per 1 vol
    point"``, no convention fork) and stored beside the value so the cell stays
    self-describing like the other dollar Greeks.

    **Mirror Greeks (T-mirror-greeks-putcall):** at each solved cell the projection also
    prices the *opposite* option right at the **same** fitted IV — a put for a call-wing
    band, a call for a put-wing band. ``price_mirror``, ``delta_mirror``, ``theta_mirror``,
    and ``rho_mirror`` carry those opposite-right values. ``gamma`` and ``vega`` are
    intentionally omitted from the mirror: put-call parity guarantees Γcall == Γput and
    νcall == νput at one IV and one strike, so they are already in the primary fields.
    The ``dollar_delta_mirror``, ``dollar_theta_mirror``, and ``dollar_rho_mirror`` fields
    carry the monetized forms under the same conventions as the primary dollar Greeks.
    All seven mirror fields are additive-nullable — a partition written before this lane
    reads them back ``None`` unchanged. They let the front draw the full S-shaped delta
    curve (both branches: call 1→0 and put 0→−1) and render theta/rho for both sides
    without a second fit, second ingestion, or any surface change.
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
    # RT-Vega (running-time / annualised vega = vega/sqrt(T), ADR 0050) per strike, raw + cash,
    # additive-nullable; the unit is unforked ("$ per 1 vol point") and stored for self-description.
    rt_vega: float | None = None
    dollar_rt_vega: float | None = None
    dollar_rt_vega_unit: str | None = None
    # Which fitted surface supplied this cell's IV (ADR 0048). Part of the primary key. Defaults
    # to ``combined`` so every pre-per-side row and fixture reads back as the legacy single
    # surface unchanged; ``put``/``call`` rows are additive, emitted only where the per-side fits
    # are supplied.
    surface_side: str = SURFACE_SIDE_COMBINED
    # Mirror Greeks (T-mirror-greeks-putcall): the opposite option right at the same fitted IV.
    # price_mirror is the opposite-right model price; delta_mirror / theta_mirror / rho_mirror
    # are its Greeks. dollar_* counterparts follow the same monetization as the primary Greeks.
    # gamma / vega are omitted — they are identical call vs put (put-call parity), so the primary
    # gamma / vega already carry the shared value. All additive-nullable: None on pre-lane rows.
    price_mirror: float | None = None
    delta_mirror: float | None = None
    theta_mirror: float | None = None
    rho_mirror: float | None = None
    dollar_delta_mirror: float | None = None
    dollar_theta_mirror: float | None = None
    dollar_rho_mirror: float | None = None

    def __post_init__(self) -> None:
        if self.surface_side not in SURFACE_SIDES:
            raise ContractValidationError(
                "projected_option_analytics", "surface_side", self.surface_side,
                f"must be one of {SURFACE_SIDES}",
            )


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
    """Stress PnL for one contract under one scenario shock.

    ``rate_shock`` is additive-nullable (``float | None``): it was added after the first three
    shock axes, so a partition written before the rate axis reads it back ``None`` (the
    schema-evolution rule). It persists the scenario's absolute rate move so a stored rate
    scenario is distinguishable on replay — a ``rate_+0.0025`` cell no longer reads identically
    to ``rate_+0.0`` (blueprint Part XV/XIX provenance)."""

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
    """By-Greek decomposition of one line's (or one book's) stress PnL under one scenario.

    The cross-Greek axis (2C): the ADR-0006 full reprice is the truth, the Taylor split is
    its explanation, and ``residual = full_reprice_pnl - approx_pnl`` is the honest accuracy
    of that explanation — always carried, never silently dropped. The named contributions
    (``delta_pnl``/``gamma_pnl``/``vega_pnl``/``theta_pnl`` and the second-order
    ``rho_pnl``/``vanna_pnl``/``volga_pnl``) are dollar PnL, book-additive, so a book
    record's terms are the term-wise sum of its lines'. ``approx_pnl`` is their lumped sum
    (the local Taylor number — now through Volga, TARGET §2.5 / §7.2).

    ``rho_pnl``/``vanna_pnl``/``volga_pnl`` are additive-nullable (``float | None``): they
    were added after the first four, so a partition written before this lane reads them
    back ``None`` (the schema-evolution rule), and ``approx_pnl`` already folds them in
    when present. The rate term is non-zero only when the move carries a rate change (the
    scenario grid holds rates fixed → ``rho_pnl == 0`` there; the realized day-over-day
    path drives it); vanna needs a joint spot-and-vol move, volga a vol move.

    ``level`` is ``"position"`` for a per-line record or ``"book"`` for the aggregated
    record; a book record carries the book sentinel in ``contract_key`` so the two never
    collide in the primary key. ``within_tolerance`` is the residual verdict against the
    carried ``residual_abs_tol``/``residual_rel_tol`` (accepted when
    ``|residual| <= max(abs_tol, rel_tol*|full_reprice_pnl|)``); it is ``False`` whenever a
    contribution or the full reprice is non-finite (a labeled diagnostic, not silent
    agreement). ``attribution_version`` brands the decomposition conventions used.
    """

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


@dataclass(frozen=True, slots=True)
class BookGreeks:
    """One row of a composed book's net Greeks — a single layer, or the combined book (WS 2D).

    A *book* is an operator's named, ordered set of sub-strategies (each a 2A position set)
    layered into one. This flat record carries the net Greeks for **one** of those rows,
    discriminated by ``level``: ``"layer"`` for a single sub-strategy's net, ``"book"`` for the
    combined aggregate over the union of all layers. The combined row is the **additive sum** of
    the layer rows (ADR 0006), provably equal to the flat aggregate over the union — so a book is
    a *view* that layers and sums, never a re-solve. ``layer_label`` is the operator's label for a
    layer row and the ``"__book__"`` sentinel for the combined row (so the two never collide in the
    primary key, mirroring 2C's level/sentinel pattern); ``layer_index`` is the display order
    (``-1`` for the combined row).

    Net Greeks are carried in **both** representations side by side: decimal per-unit
    (``net_delta``/``gamma``/``vega``/``theta`` — contract-level ``per_unit·multiplier·quantity``,
    the additive quantities) and dollar (``dollar_*``, the per-line monetization of WS-1F's
    ``pricing/dollar_greeks.py`` summed across positions — book-additive), each dollar number
    paired with its unit string. Dollars are currency-tagged cash and are not summed across
    currencies (ADR 0006).
    """

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
    """One daily, as-of, contract-typed strategy-entry signal reading (TARGET §4 R3 / §3).

    The persisted product of the signal layer: the ρ̄ / IV-rank / RV−IV / term-slope readings
    the §3 strategy book triggers on, derived once a day from the as-of surfaces, price history,
    and index weights, and read back by a strategy as its entry input (the §6 no-look-ahead bar
    is the partition: a reading lives under the ``trade_date`` it was computed *as of*).

    The grain is one scalar reading per ``(snapshot_ts, provider, signal_kind, subject,
    tenor_label)``:

    * ``underlying`` is the **book context** — the index whose strategy reads this signal
      (e.g. ``"SX5E"``); it is the partition's grouping symbol, so a strategy reads the whole
      day's signal set for its index in one partition. It is *not* the name the reading is
      about — that is ``subject``.
    * ``signal_kind`` is the signal family, the string value of the strategy-layer
      ``SignalKind`` (``"implied_correlation"`` / ``"iv_rank"`` / ``"iv_vs_realized"`` /
      ``"term_structure_slope"``). Carried as a plain ``str`` because the contracts seam is
      blind to the alpha layer that owns the enum.
    * ``subject`` is *what* the reading is on — the index symbol for an index-level reading
      (book-wide ρ̄), a single-name ticker for a per-name reading (a constituent's IV-rank).
    * ``tenor_label`` is the tenor the reading was taken at (``"3m"``), or a ``"front:back"``
      pillar pair for a term-structure slope. Per-tenor by construction (ρ̄ is a per-tenor
      signal); a signal with no tenor still names the pillar it used so the row is self-describing.
    * ``value`` is the scalar — not sign-constrained: ρ̄ can fall outside ``[-1, 1]`` and an
      RV−IV or term slope is signed; a constraint would hide a real diagnostic.

    Derived (``requires_provenance`` / ``requires_source_snapshot_ts``): every reading carries
    the stamp naming the analytics rows and bars it was computed from, and the source snapshot
    timestamp it is as-of.
    """

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


# The labelled per-name results the constituent capture lane records, one per attempted
# constituent (S1 dispersion / EMERGENCY-constituent-lane-activation). The set is closed: a
# name's fate on a given close is exactly one of these, so the entitlement question — which
# of the index's heaviest names actually return an option chain on this account — is answered
# per name, never a silent absence.
#   captured   — the name's option chain was captured (``n_options`` carries the count)
#   no_options — the name resolved to a conid but lists no qualifiable options (a real outcome)
#   unentitled — the account is not entitled to the name's option data (a recorded, expected gap)
#   unresolved — the name's underlying conid would not resolve (a ticker IBKR does not list here)
CONSTITUENT_OUTCOMES = ("captured", "no_options", "unentitled", "unresolved")


@dataclass(frozen=True, slots=True)
class ConstituentCaptureOutcome:
    """One constituent's labelled outcome from one close's widened capture (S1 dispersion).

    The per-name ledger the widened EOD capture writes: for each of the index's point-in-time
    top-N constituents it *attempted*, exactly one labelled :data:`CONSTITUENT_OUTCOMES` row
    — so a name that returns no chain is a recorded ``no_options``/``unentitled``/``unresolved``
    fact, never a silent absence. ``underlying`` is the constituent symbol (the partition key,
    so a name lands under ``…/underlying=<SYMBOL>``); ``index`` names the basket it was selected
    from; ``weight`` is its as-of index weight (what put it in the top-N); ``rank`` its 1-based
    position in the weight ranking. ``n_options`` is the captured option-leg count for a
    ``captured`` outcome and ``0`` otherwise. ``detail`` is a one-line human reason (e.g. the
    unresolved ticker, or the entitlement error text), never a generic banner.

    ``run_ts`` places the row on the time-partitioned layout (the close instant the capture ran
    at); the ``(run_id, index, underlying)`` key makes a re-fire of the same close idempotent.
    """

    run_id: str
    run_ts: datetime
    index: str
    underlying: str
    outcome: str
    rank: int
    weight: float
    n_options: int
    detail: str

    def __post_init__(self) -> None:
        if self.outcome not in CONSTITUENT_OUTCOMES:
            raise ContractValidationError(
                "ConstituentCaptureOutcome",
                "outcome",
                self.outcome,
                f"must be one of {CONSTITUENT_OUTCOMES}",
            )
        if self.n_options < 0:
            raise ContractValidationError(
                "ConstituentCaptureOutcome",
                "n_options",
                self.n_options,
                "captured option count must be non-negative",
            )
        if self.outcome != "captured" and self.n_options != 0:
            raise ContractValidationError(
                "ConstituentCaptureOutcome",
                "n_options",
                self.n_options,
                "only a 'captured' outcome carries a non-zero option count",
            )


# A leg names exactly one tradable thing: an option grid cell, or the underlying itself.
INSTRUMENT_KINDS = ("option", "stock")
# Side is explicit and must agree with the quantity sign — a "long" leg is a positive
# quantity, a "short" leg a negative one. The pair is carried (not just the sign) so the
# composition reads the way an operator thinks, and a contradiction is a rejected contract.
LEG_SIDES = ("long", "short")


@dataclass(frozen=True, slots=True)
class BasketLeg:
    """One leg of a multi-leg basket: a signed, side-labelled reference to one instrument.

    An **option** leg references one cell of the WS-1F analytics grid by its coordinates —
    ``(underlying, tenor_label, delta_band)`` — because :class:`ProjectedOptionAnalytics`
    is addressed by that grid coordinate, not by a canonical ``instrument_key`` (the grid
    has no per-contract expiry/strike; it has a tenor and a delta band). A **stock** leg
    references the underlying itself (its spot exposure) and carries no tenor/band.

    ``side``/``quantity`` consistency is enforced at construction: a ``"long"`` leg with a
    negative quantity (or vice versa), a zero quantity, or a non-finite quantity is a
    malformed contract, rejected with a structured :class:`ContractValidationError` that
    carries the offending value — never silently normalised. ``quantity`` is already signed
    by the side, so downstream code multiplies by it directly and never re-applies the side.

    ``surface_side`` selects which fitted vol surface this leg's analytics cell is read from
    (ADR 0048): ``"combined"`` (the default — the forward-backing / attribution reference, so
    an unspecified leg is unchanged) or a wing, ``"put"`` / ``"call"``. The wing is the
    explicit opt-in a wing-aware strategy uses to price its put leg off the put surface and its
    call leg off the call surface (an S1 dispersion straddle) instead of mutualising one IV.
    It is **not** the option right — the right is still fixed by the band's ``…p`` / ``…c``
    suffix; ``surface_side`` only picks the surface the cell's IV comes from.
    """

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
    """An ordered, named, side-aware set of legs priced against one analytics snapshot.

    A basket is the composed envelope over a set of signed :class:`BasketLeg` references —
    not a second position type. Its price and Greeks are defined as the **book-additive
    sum** over its legs of the dollar Greeks the WS-1F grid already produced (option legs)
    plus the linear spot delta (stock legs); see :mod:`algotrading.infra.risk.multileg`.

    The look-ahead anchor is ``trade_date`` (a date, matching the analytics read API): the
    basket prices off *that day's* grid and a later snapshot does not change it. ``underlying``
    scopes which grid is read; ``provider`` optionally narrows the provider-partitioned read
    (``None`` reads across providers). An **empty** ``legs`` is a valid, labelled-empty
    basket — not rejected here.

    ``strategy_id`` is the **strategy-identity stamp** (additive-nullable, ``None`` on an
    operator-authored or pre-strategy-layer basket): the identity of the
    :class:`~algotrading.strategy.Strategy` that emitted this position set, carried so the
    same emitted object can be (a) layered as a *named book layer* by 2D composition
    (``risk/book.py``) and (b) grouped by strategy for per-strategy P&L attribution
    (TARGET §7.2). Defined by the strategy spine (``packages/strategy``) and consumed by
    those two infra lanes — infra never reads strategy *logic*, only this opaque label.
    When present it must be a non-empty string; the additive default keeps every existing
    basket valid unchanged.
    """

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
