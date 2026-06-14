"""Compute and persist the daily strategy-entry signal set, as-of and look-ahead clean.

The orchestration tying the pure signal math (``correlation`` / ``term_structure`` /
``realized_volatility`` / ``iv_history``) to the as-of store: read the surfaces, price
history, and index weights *as they stood on a date*, compute every signal that the data can
answer, and persist them as :class:`~algotrading.infra.contracts.StrategySignal` rows. The §6
no-look-ahead bar is structural — every read is gated by ``as_of`` (``trade_date <= as_of``,
live partition only), so a replay of an old day resolves only that day's data.

Three pieces, the same split S1 uses (pure rule + I/O seam):

* :func:`read_signal_inputs` — the as-of store reads, gathering the raw inputs.
* :func:`build_signals` — pure: inputs in, ``StrategySignal`` rows out, one shared provenance
  stamp. A signal the inputs cannot answer is **omitted** (a labelled absence), never written
  as a fabricated value.
* :func:`persist_signal_set` — read → build → write, the daily batch entry point.

The signal-kind strings are mirrored from the strategy layer's ``SignalKind`` enum — the
contracts seam is blind to alpha (it cannot import the enum), so the canonical values live
there and a strategy-layer test pins these constants against them so the seam cannot drift.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from algotrading.core.provenance import SourceRecordRef, snapshot_stamp, source_ref
from algotrading.infra.contracts import SURFACE_SIDE_COMBINED, StrategySignal
from algotrading.infra.storage import ParquetStore
from algotrading.infra.universe import BasketMember, members, top_n_by_weight

from .correlation import ImpliedCorrelationError, implied_correlation
from .iv_history import IvRankError, iv_rank
from .realized_volatility import (
    TRADING_DAYS_PER_YEAR,
    realized_minus_implied,
    realized_volatility,
)
from .term_structure import TermStructureError, term_structure_slope

# This layer's code version, folded into every reading's provenance stamp.
SIGNAL_LAYER_VERSION = "signal-layer-1"

# The ATM-call pillar of the projection grid (``projection.py``): the at-the-money-forward
# strike whose combined-surface IV is the name's ATM vol. An internal axis label, not a tunable.
ATM_DELTA_BAND = "atm"

# The persisted ``signal_kind`` strings. They MIRROR the strategy layer's ``SignalKind`` enum
# values; infra cannot import that enum (blind to alpha), so the values live here as the
# storage form and ``packages/strategy`` pins them against the enum in a test.
SIGNAL_KIND_IMPLIED_CORRELATION = "implied_correlation"
SIGNAL_KIND_IV_RANK = "iv_rank"
SIGNAL_KIND_IV_VS_REALIZED = "iv_vs_realized"
SIGNAL_KIND_TERM_STRUCTURE_SLOPE = "term_structure_slope"

_ANALYTICS_TABLE = "projected_option_analytics"
_BARS_TABLE = "daily_bar"

# One assembled reading before it becomes a row: (signal_kind, subject, tenor_label, value).
_SignalRow = tuple[str, str, str, float]


@dataclass(frozen=True, slots=True)
class SignalConfig:
    """The injected config for one index's daily signal computation (ADR 0028 — DI, no literals).

    ``reference_tenor`` is the single tenor the per-name range signals (IV-rank, RV−IV) are
    taken at; ``term_slope_front`` / ``term_slope_back`` name the two pillars the term slope
    spans. ``basket_size`` selects the ρ̄ universe — ``None`` uses the full as-of basket, an
    int uses the top-``n`` by weight (the course's top-10 dispersion universe). The lookbacks
    are calendar-day windows for the trailing history reads.
    """

    index: str
    provider: str
    reference_tenor: str
    term_slope_front: str
    term_slope_back: str
    iv_history_lookback_days: int
    realized_vol_lookback_days: int
    periods_per_year: float = TRADING_DAYS_PER_YEAR
    basket_size: int | None = None


@dataclass(frozen=True, slots=True)
class SignalInputs:
    """The as-of-read raw inputs for one signal computation — what :func:`build_signals` reads.

    ``snapshot_ts`` / ``source_snapshot_ts`` are the source surface's snapshot timestamp (the
    day's daily snapshot the signals are computed from); ``None`` when no surface was banked
    for ``as_of`` (then nothing is built). ``atm_vol_by_subject`` maps each subject (the index
    and its constituents) to its tenor→ATM-vol map; ``weights`` carries the constituents that
    have a known index weight; ``iv_history_by_subject`` is the trailing ATM-vol window at the
    reference tenor; ``realized_vol_by_subject`` is the annualized realized vol over the window.
    """

    as_of: date
    snapshot_ts: datetime | None
    source_snapshot_ts: datetime | None
    atm_vol_by_subject: Mapping[str, Mapping[str, float]]
    weights: Mapping[str, float]
    iv_history_by_subject: Mapping[str, tuple[float, ...]]
    realized_vol_by_subject: Mapping[str, float]
    subjects: tuple[str, ...] = field(default_factory=tuple)


def _resolve_basket(
    store: ParquetStore, config: SignalConfig, as_of: date
) -> tuple[BasketMember, ...]:
    """The as-of basket for ρ̄: the full membership, or the top-``basket_size`` by weight."""
    if config.basket_size is None:
        return members(store, config.index, as_of)
    return top_n_by_weight(store, config.index, as_of, config.basket_size)


def _atm_vol_by_tenor(
    store: ParquetStore, subject: str, config: SignalConfig, as_of: date
) -> tuple[dict[str, float], datetime | None]:
    """Combined-surface ATM IV per tenor for one subject on ``as_of`` (and its snapshot ts)."""
    rows = store.read(
        _ANALYTICS_TABLE, trade_date=as_of, underlying=subject, provider=config.provider
    )
    by_tenor: dict[str, float] = {}
    snapshot_ts: datetime | None = None
    for row in rows:
        if row.surface_side == SURFACE_SIDE_COMBINED and row.delta_band == ATM_DELTA_BAND:
            by_tenor[row.tenor_label] = row.implied_vol
            snapshot_ts = row.snapshot_ts
    return by_tenor, snapshot_ts


def _iv_history(
    store: ParquetStore, subject: str, config: SignalConfig, as_of: date
) -> tuple[float, ...]:
    """Trailing combined ATM IVs at the reference tenor, oldest first, through ``as_of``."""
    start = as_of - timedelta(days=config.iv_history_lookback_days)
    rows = store.read(
        _ANALYTICS_TABLE,
        underlying=subject,
        provider=config.provider,
        start_date=start,
        end_date=as_of,
    )
    dated = [
        (row.snapshot_ts, row.implied_vol)
        for row in rows
        if row.surface_side == SURFACE_SIDE_COMBINED
        and row.delta_band == ATM_DELTA_BAND
        and row.tenor_label == config.reference_tenor
    ]
    return tuple(iv for _, iv in sorted(dated, key=lambda pair: pair[0]))


def _realized_vol(
    store: ParquetStore, subject: str, config: SignalConfig, as_of: date
) -> float | None:
    """Annualized realized vol over the trailing close window, or ``None`` if too few bars."""
    start = as_of - timedelta(days=config.realized_vol_lookback_days)
    bars = store.read(
        _BARS_TABLE,
        underlying=subject,
        provider=config.provider,
        start_date=start,
        end_date=as_of,
    )
    closes = [bar.close for bar in sorted(bars, key=lambda bar: bar.trade_date)]
    if len(closes) < 2:
        return None
    return realized_volatility(closes, periods_per_year=config.periods_per_year)


def read_signal_inputs(store: ParquetStore, config: SignalConfig, as_of: date) -> SignalInputs:
    """Gather the as-of inputs for one index's signal set — every read gated by ``as_of``.

    Resolves the basket as it stood on ``as_of`` and reads, per subject (index + constituents),
    the combined-surface ATM vols, the trailing IV window, and the realized vol — all from the
    live partitions at or before ``as_of``, so no read reaches past the date.
    """
    basket = _resolve_basket(store, config, as_of)
    weights = {member.constituent: member.weight for member in basket if member.weight is not None}
    subjects = (config.index, *(member.constituent for member in basket))

    atm_vol_by_subject: dict[str, Mapping[str, float]] = {}
    iv_history_by_subject: dict[str, tuple[float, ...]] = {}
    realized_vol_by_subject: dict[str, float] = {}
    snapshot_ts: datetime | None = None
    for subject in subjects:
        by_tenor, subject_snapshot = _atm_vol_by_tenor(store, subject, config, as_of)
        if by_tenor:
            atm_vol_by_subject[subject] = by_tenor
            snapshot_ts = snapshot_ts or subject_snapshot
        history = _iv_history(store, subject, config, as_of)
        if history:
            iv_history_by_subject[subject] = history
        realized = _realized_vol(store, subject, config, as_of)
        if realized is not None:
            realized_vol_by_subject[subject] = realized

    return SignalInputs(
        as_of=as_of,
        snapshot_ts=snapshot_ts,
        source_snapshot_ts=snapshot_ts,
        atm_vol_by_subject=atm_vol_by_subject,
        weights=weights,
        iv_history_by_subject=iv_history_by_subject,
        realized_vol_by_subject=realized_vol_by_subject,
        subjects=subjects,
    )


def _correlation_rows(inputs: SignalInputs, config: SignalConfig) -> list[_SignalRow]:
    """ρ̄ per tenor on the index subject — one row per tenor the basket can answer.

    For each tenor the index prices, collect the (weight, ATM-vol) of every constituent that
    has both, and solve Eq. 23 for ρ̄. A degenerate tenor (no off-diagonal pair) is omitted.
    Incomplete per-name surface coverage biases ρ̄ (the cross term is understated) — R2-grade
    coverage is assumed; the reading is over the names actually present, with their real weights.
    """
    index_vols = inputs.atm_vol_by_subject.get(config.index, {})
    rows: list[_SignalRow] = []
    for tenor, index_vol in sorted(index_vols.items()):
        paired = [
            (weight, inputs.atm_vol_by_subject[name][tenor])
            for name, weight in inputs.weights.items()
            if name in inputs.atm_vol_by_subject and tenor in inputs.atm_vol_by_subject[name]
        ]
        if not paired:
            continue
        weights = [w for w, _ in paired]
        vols = [v for _, v in paired]
        try:
            rho_bar = implied_correlation(weights, vols, index_vol)
        except ImpliedCorrelationError:
            continue
        rows.append((SIGNAL_KIND_IMPLIED_CORRELATION, config.index, tenor, rho_bar))
    return rows


def _per_subject_rows(inputs: SignalInputs, config: SignalConfig) -> list[_SignalRow]:
    """Term-slope, RV−IV and IV-rank per subject — each omitted where its inputs cannot answer."""
    rows: list[_SignalRow] = []
    slope_tenor = f"{config.term_slope_front}:{config.term_slope_back}"
    for subject in inputs.subjects:
        by_tenor = inputs.atm_vol_by_subject.get(subject, {})
        try:
            slope = term_structure_slope(
                by_tenor, front=config.term_slope_front, back=config.term_slope_back
            )
            rows.append((SIGNAL_KIND_TERM_STRUCTURE_SLOPE, subject, slope_tenor, slope))
        except TermStructureError:
            pass

        reference_iv = by_tenor.get(config.reference_tenor)
        realized = inputs.realized_vol_by_subject.get(subject)
        if reference_iv is not None and realized is not None:
            spread = realized_minus_implied(realized, reference_iv)
            rows.append((SIGNAL_KIND_IV_VS_REALIZED, subject, config.reference_tenor, spread))

        history = inputs.iv_history_by_subject.get(subject)
        if reference_iv is not None and history:
            try:
                rank = iv_rank(reference_iv, history)
                rows.append((SIGNAL_KIND_IV_RANK, subject, config.reference_tenor, rank))
            except IvRankError:
                pass
    return rows


def build_signals(
    inputs: SignalInputs,
    config: SignalConfig,
    *,
    calc_ts: datetime,
    config_hashes: Mapping[str, str],
) -> tuple[StrategySignal, ...]:
    """Pure: assemble the ``StrategySignal`` rows from already-read inputs, one shared stamp.

    Every reading the inputs can answer becomes a row under the index's book context
    (``underlying = config.index``). Nothing is built when no surface was banked for the day
    (no ``snapshot_ts``) — a labelled absence, not an empty fabricated set. The provenance
    stamp is snapshot-wide (the set is one computation), naming the source surfaces and bars.
    """
    if inputs.snapshot_ts is None or inputs.source_snapshot_ts is None:
        return ()

    readings = _correlation_rows(inputs, config) + _per_subject_rows(inputs, config)
    if not readings:
        return ()

    surface_refs = tuple(
        source_ref(_ANALYTICS_TABLE, inputs.source_snapshot_ts, subject)
        for subject in inputs.subjects
        if subject in inputs.atm_vol_by_subject
    )
    bar_refs = tuple(
        source_ref(_BARS_TABLE, subject) for subject in sorted(inputs.realized_vol_by_subject)
    )
    refs: tuple[SourceRecordRef, ...] = surface_refs + bar_refs
    stamp = snapshot_stamp(
        calc_ts=calc_ts,
        code_version=SIGNAL_LAYER_VERSION,
        config_hashes=config_hashes,
        source_snapshot_ts=inputs.source_snapshot_ts,
        source_records=refs,
    )
    return tuple(
        StrategySignal(
            snapshot_ts=inputs.snapshot_ts,
            provider=config.provider,
            underlying=config.index,
            signal_kind=signal_kind,
            subject=subject,
            tenor_label=tenor_label,
            value=value,
            source_snapshot_ts=inputs.source_snapshot_ts,
            provenance=stamp,
        )
        for signal_kind, subject, tenor_label, value in readings
    )


def persist_signal_set(
    store: ParquetStore,
    config: SignalConfig,
    as_of: date,
    *,
    calc_ts: datetime,
    config_hashes: Mapping[str, str],
) -> tuple[StrategySignal, ...]:
    """Read the as-of inputs, build the signal set, and persist it. The daily batch entry point.

    Returns the rows written (empty when the day had no surface to compute from). Writes only
    when there is something to write, so an empty day leaves the store untouched.
    """
    inputs = read_signal_inputs(store, config, as_of)
    rows = build_signals(inputs, config, calc_ts=calc_ts, config_hashes=config_hashes)
    if rows:
        store.write("strategy_signals", list(rows))
    return rows
