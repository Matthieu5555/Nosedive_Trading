"""The date-range batch driver: reconstruct every stored day in a range, in order.

Historical reconstruction is not a second compute path — it is :func:`actor.run_day`
run over a date range. Each day reads its raw events off the immutable raw layer and
runs the *identical* analytics as live (ADR 0007, decision 4), so there is nothing to
drift. This module adds only the batch layer the spec asks for on top of that one
function: walk the requested dates in order, flag a day whose raw partition is absent
instead of fabricating an empty result, optionally write each day's restatement under
a version so a newer-code run lands beside the older analytic rather than over it, and
return a structured :class:`ReconstructionReport` of what ran and what was skipped.

The driver never invents market data. A day with no stored raw partition is reported
:data:`report.MISSING` with no outputs at all; a day whose raw partition exists but
yields no derived records is reported :data:`report.EMPTY`. Those are different facts
and the report keeps them different.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, timedelta

import structlog
from algotrading.core.config import PlatformConfig
from algotrading.infra.actor import ActorOutputs, persist_outputs, run_analytics
from algotrading.infra.actor.valuation_join import default_exercise_style
from algotrading.infra.collectors import replay_day
from algotrading.infra.contracts import (
    ForwardCurvePoint,
    InstrumentKey,
    InstrumentMaster,
    IvPoint,
    MarketStateSnapshot,
    Position,
    PricingResult,
    RiskAggregate,
    ScenarioResult,
    SurfaceGrid,
    SurfaceParameters,
    table_for_contract,
)
from algotrading.infra.storage import ParquetStore

from .report import (
    EMPTY,
    MISSING,
    RECONSTRUCTED,
    DayReconstruction,
    ReconstructionReport,
)

_LOGGER = structlog.get_logger("orchestration.reconstruction")

_RAW_MARKET_EVENTS = "raw_market_events"


def stored_trade_dates(store: ParquetStore) -> tuple[date, ...]:
    """The distinct trade dates that have a stored raw partition, ascending.

    Reads the raw layer's partition index (``list_partitions``) and dedups the dates,
    so this is the set of days a reconstruction *could* replay. A day not in this set
    is exactly the "missing partition" the batch driver flags.
    """
    dates = {trade_date for trade_date, _underlying in store.list_partitions(_RAW_MARKET_EVENTS)}
    return tuple(sorted(dates))


def _date_range(start: date, end: date) -> tuple[date, ...]:
    """Every calendar date from ``start`` to ``end`` inclusive, ascending.

    Raises :class:`ValueError` if ``end`` precedes ``start`` — an inverted range is a
    caller bug, surfaced loudly rather than silently yielding nothing.
    """
    if end < start:
        raise ValueError(f"end {end.isoformat()} precedes start {start.isoformat()}")
    span = (end - start).days
    return tuple(start + timedelta(days=offset) for offset in range(span + 1))


def reconstruct_day(
    store: ParquetStore,
    trade_date: date,
    positions: Sequence[Position],
    *,
    instruments: Sequence[InstrumentKey],
    masters: Sequence[InstrumentMaster],
    config: PlatformConfig,
    config_hashes: Mapping[str, str],
    as_of: datetime,
    calc_ts: datetime,
    exercise_style_for: Callable[[InstrumentKey], str] = default_exercise_style,
    moneyness_buckets: tuple[float, ...] | None = None,
    version: str | None = None,
    persist: bool = True,
    correlation_id: str = "",
) -> DayReconstruction:
    """Reconstruct one trade date off the raw layer, optionally under a version.

    Reads the day's raw events via :func:`collectors.replay_day`. If the day has no
    stored raw partition, returns a :data:`report.MISSING` outcome with no outputs —
    never a fabricated empty :class:`ActorOutputs`. Otherwise runs the identical actor
    compute as live (:func:`actor.run_analytics`) and, when ``persist`` is True, writes
    each derived table under ``version`` so a restatement lands in its own
    ``version=<V>`` sub-partition beside any existing analytic. A day that runs but
    produces nothing is reported :data:`report.EMPTY`.

    This calls ``run_analytics`` + a versioned ``persist`` rather than ``actor.run_day``
    because ``run_day`` always persists unversioned; the *compute* is identical, which
    is what keeps this on the one code path.
    """
    log = _LOGGER.bind(
        correlation_id=correlation_id,
        trade_date=trade_date.isoformat(),
        version=version or "",
    )
    events = replay_day(store, trade_date)
    if not events:
        log.info("reconstruction.day.missing")
        return DayReconstruction(
            trade_date=trade_date,
            status=MISSING,
            outputs=None,
            version=version,
            record_count=0,
            reason="no stored raw partition for this trade date",
        )

    outputs = run_analytics(
        events,
        positions,
        instruments=instruments,
        masters=masters,
        config=config,
        config_hashes=config_hashes,
        as_of=as_of,
        calc_ts=calc_ts,
        exercise_style_for=exercise_style_for,
        moneyness_buckets=moneyness_buckets,
    )

    count = _record_count(outputs)
    if outputs.is_empty():
        log.info("reconstruction.day.empty", event_count=len(events))
        return DayReconstruction(
            trade_date=trade_date,
            status=EMPTY,
            outputs=outputs,
            version=version,
            record_count=0,
            reason="raw partition present but produced no derived records",
        )

    if persist:
        _persist_outputs(store, outputs, version=version)
        log.info("reconstruction.day.persisted", record_count=count)

    log.info("reconstruction.day.reconstructed", record_count=count)
    return DayReconstruction(
        trade_date=trade_date,
        status=RECONSTRUCTED,
        outputs=outputs,
        version=version,
        record_count=count,
    )


def reconstruct_range(
    store: ParquetStore,
    start: date,
    end: date,
    positions: Sequence[Position],
    *,
    instruments: Sequence[InstrumentMaster] | Sequence[InstrumentKey],
    masters: Sequence[InstrumentMaster],
    config: PlatformConfig,
    config_hashes: Mapping[str, str],
    as_of_for: Callable[[date], datetime],
    calc_ts_for: Callable[[date], datetime],
    exercise_style_for: Callable[[InstrumentKey], str] = default_exercise_style,
    moneyness_buckets: tuple[float, ...] | None = None,
    version: str | None = None,
    persist: bool = True,
    correlation_id: str = "",
) -> ReconstructionReport:
    """Reconstruct every trade date in ``[start, end]`` in order, into one report.

    Walks the inclusive calendar range ascending and reconstructs each day with
    :func:`reconstruct_day`. ``as_of_for``/``calc_ts_for`` map a trade date to that
    day's injected market-snapshot and computation timestamps — they are injected (not
    read from a clock) so the run is reproducible, and they vary per day because each
    day's stamps key on its own date. Days with no stored raw partition are flagged
    :data:`report.MISSING` in the report and produce no output; nothing is
    interpolated across a gap. ``version`` (when given) restates the whole range under
    that version, leaving any prior analytic intact.
    """
    instrument_keys = _as_instrument_keys(instruments)
    log = _LOGGER.bind(
        correlation_id=correlation_id,
        start=start.isoformat(),
        end=end.isoformat(),
        version=version or "",
    )
    log.info("reconstruction.range.start")

    days = tuple(
        reconstruct_day(
            store,
            trade_date,
            positions,
            instruments=instrument_keys,
            masters=masters,
            config=config,
            config_hashes=config_hashes,
            as_of=as_of_for(trade_date),
            calc_ts=calc_ts_for(trade_date),
            exercise_style_for=exercise_style_for,
            moneyness_buckets=moneyness_buckets,
            version=version,
            persist=persist,
            correlation_id=correlation_id,
        )
        for trade_date in _date_range(start, end)
    )

    report = ReconstructionReport(start=start, end=end, version=version, days=days)
    log.info(
        "reconstruction.range.done",
        day_count=len(report.days),
        reconstructed=len(report.reconstructed_dates),
        missing=len(report.missing_dates),
    )
    return report


def _persist_outputs(
    store: ParquetStore, outputs: ActorOutputs, *, version: str | None
) -> None:
    """Write each non-empty output tuple to its table under ``version``.

    The version-threaded twin of :func:`actor.persist_outputs`: same table routing
    (``contracts.table_for_contract``) and same write-ahead-validated ``store.write``,
    but it passes ``version`` through so a restatement lands in its own
    ``version=<V>`` sub-partition. With ``version=None`` it is byte-for-byte the
    unversioned replace-in-place path — the actor's own persist — so the live layout is
    unchanged; an explicit version is the only thing that opens a coexisting
    sub-partition (ADR 0007, decision 3). When ``version`` is None this defers to
    :func:`actor.persist_outputs` directly so the live path has exactly one
    implementation.
    """
    if version is None:
        persist_outputs(store, outputs)
        return
    tables = (
        (MarketStateSnapshot, outputs.snapshots),
        (ForwardCurvePoint, outputs.forwards),
        (IvPoint, outputs.iv_points),
        (SurfaceParameters, outputs.surface_parameters),
        (SurfaceGrid, outputs.surface_grid),
        (PricingResult, outputs.pricings),
        (RiskAggregate, outputs.risk_aggregates),
        (ScenarioResult, outputs.scenarios),
    )
    for contract_type, records in tables:
        if not records:
            continue
        store.write(table_for_contract(contract_type), list(records), version=version)


def _record_count(outputs: ActorOutputs) -> int:
    """Total derived records across every output tuple of one actor run."""
    return (
        len(outputs.snapshots)
        + len(outputs.forwards)
        + len(outputs.iv_points)
        + len(outputs.surface_parameters)
        + len(outputs.surface_grid)
        + len(outputs.pricings)
        + len(outputs.risk_aggregates)
        + len(outputs.scenarios)
    )


def _as_instrument_keys(
    instruments: Sequence[InstrumentMaster] | Sequence[InstrumentKey],
) -> tuple[InstrumentKey, ...]:
    """Accept either raw instrument keys or masters; the actor wants the keys.

    A convenience so a caller that already has the day's masters does not have to
    unpack the keys by hand; passing keys directly is the common path.
    """
    keys: list[InstrumentKey] = []
    for item in instruments:
        if isinstance(item, InstrumentMaster):
            keys.append(item.instrument)
        else:
            keys.append(item)
    return tuple(keys)
