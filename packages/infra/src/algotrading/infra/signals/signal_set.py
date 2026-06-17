from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from algotrading.core.config import SignalEntryConfig
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

SIGNAL_LAYER_VERSION = "signal-layer-1"

ATM_DELTA_BAND = "atm"

SIGNAL_KIND_IMPLIED_CORRELATION = "implied_correlation"
SIGNAL_KIND_IV_RANK = "iv_rank"
SIGNAL_KIND_IV_VS_REALIZED = "iv_vs_realized"
SIGNAL_KIND_TERM_STRUCTURE_SLOPE = "term_structure_slope"

_ANALYTICS_TABLE = "projected_option_analytics"
_BARS_TABLE = "daily_bar"

_SignalRow = tuple[str, str, str, float]


@dataclass(frozen=True, slots=True)
class SignalConfig:

    index: str
    provider: str
    reference_tenor: str
    term_slope_front: str
    term_slope_back: str
    iv_history_lookback_days: int
    realized_vol_lookback_days: int
    periods_per_year: float = TRADING_DAYS_PER_YEAR
    basket_size: int | None = None


def signal_config_for(
    entry: SignalEntryConfig, *, index: str, provider: str
) -> SignalConfig:
    return SignalConfig(
        index=index,
        provider=provider,
        reference_tenor=entry.reference_tenor,
        term_slope_front=entry.term_slope_front,
        term_slope_back=entry.term_slope_back,
        iv_history_lookback_days=entry.iv_history_lookback_days,
        realized_vol_lookback_days=entry.realized_vol_lookback_days,
        periods_per_year=entry.periods_per_year,
        basket_size=entry.basket_size,
    )


@dataclass(frozen=True, slots=True)
class SignalInputs:

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
    if config.basket_size is None:
        return members(store, config.index, as_of)
    return top_n_by_weight(store, config.index, as_of, config.basket_size)


def _atm_vol_by_tenor(
    store: ParquetStore, subject: str, config: SignalConfig, as_of: date
) -> tuple[dict[str, float], datetime | None]:
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
    # ρ̄ is the blueprint's index-variance diagnostic (Part II, Eq. 23): a reusable risk
    # primitive, not strategy logic. Per ADR 0051 the constituent volatilities are the REALIZED
    # vols — computed for EVERY constituent from the daily bars we backfill — not captured
    # implied ATM vols, which existed only for the retired top-N option-capture lane and biased
    # ρ̄ high (only the heaviest names had a surface). The index leg stays the index's implied
    # ATM vol, so ρ̄ is a hybrid implied/realized reading whose tenor structure comes from the
    # index's implied term structure against a single realized constituent baseline.
    index_vols = inputs.atm_vol_by_subject.get(config.index, {})
    rows: list[_SignalRow] = []
    for tenor, index_vol in sorted(index_vols.items()):
        paired = [
            (weight, inputs.realized_vol_by_subject[name])
            for name, weight in inputs.weights.items()
            if name in inputs.realized_vol_by_subject
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
    inputs = read_signal_inputs(store, config, as_of)
    rows = build_signals(inputs, config, calc_ts=calc_ts, config_hashes=config_hashes)
    if rows:
        store.write("strategy_signals", list(rows))
    return rows
