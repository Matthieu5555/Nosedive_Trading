from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, timedelta

import structlog
from algotrading.core.config import PlatformConfig
from algotrading.infra.actor import ActorOutputs, persist_outputs, run_analytics
from algotrading.infra.actor.basket import DEFAULT_PROVIDER
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
    ProjectedOptionAnalytics,
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
    dates = {trade_date for trade_date, _underlying in store.list_partitions(_RAW_MARKET_EVENTS)}
    return tuple(sorted(dates))


def _date_range(start: date, end: date) -> tuple[date, ...]:
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
    provider: str = DEFAULT_PROVIDER,
    session_open: bool = False,
    version: str | None = None,
    persist: bool = True,
    correlation_id: str = "",
) -> DayReconstruction:
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

    # ``provider`` + ``session_open=False`` MUST mirror the live EOD ``_analytics`` call: the
    # projection (``projected_option_analytics`` — the front's vol nappe) short-circuits to empty
    # when ``provider is None`` (``driver._build_projected_analytics``). Reconstruct previously
    # passed neither, so recompute-from-raw silently produced zero projected + zero pricing
    # (blueprint Part XV breach: not all derived recomputed from raw).
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
        session_open=session_open,
        provider=provider,
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
    provider: str = DEFAULT_PROVIDER,
    session_open: bool = False,
    version: str | None = None,
    persist: bool = True,
    correlation_id: str = "",
) -> ReconstructionReport:
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
            provider=provider,
            session_open=session_open,
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
        (ProjectedOptionAnalytics, outputs.projected_analytics),
    )
    for contract_type, records in tables:
        if not records:
            continue
        store.write(table_for_contract(contract_type), list(records), version=version)


def _record_count(outputs: ActorOutputs) -> int:
    return (
        len(outputs.snapshots)
        + len(outputs.forwards)
        + len(outputs.iv_points)
        + len(outputs.surface_parameters)
        + len(outputs.surface_grid)
        + len(outputs.pricings)
        + len(outputs.risk_aggregates)
        + len(outputs.scenarios)
        + len(outputs.projected_analytics)
    )


def _as_instrument_keys(
    instruments: Sequence[InstrumentMaster] | Sequence[InstrumentKey],
) -> tuple[InstrumentKey, ...]:
    keys: list[InstrumentKey] = []
    for item in instruments:
        if isinstance(item, InstrumentMaster):
            keys.append(item.instrument)
        else:
            keys.append(item)
    return tuple(keys)
