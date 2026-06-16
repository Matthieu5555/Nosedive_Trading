from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime

import structlog
from algotrading.core.config import PlatformConfig
from algotrading.infra.actor import ActorOutputs, persist_outputs, run_analytics
from algotrading.infra.collectors import (
    CollectorSummary,
    MarketDataAdapter,
    RawCollector,
    SequenceStamping,
    is_observation,
    replay_day,
)
from algotrading.infra.connectivity import Clock
from algotrading.infra.contracts import (
    InstrumentKey,
    InstrumentMaster,
    Position,
    RawMarketEvent,
)
from algotrading.infra.risk import BrokerGreeks, GreekDiscrepancy, PositionRisk, reconcile
from algotrading.infra.snapshots import SnapshotBatch, build_snapshots
from algotrading.infra.storage import ParquetStore

from .metrics import OrchestrationMetrics

_LOGGER = structlog.get_logger("orchestration")


@dataclass(frozen=True, slots=True)
class UniverseRefreshResult:

    correlation_id: str
    trade_date: date
    master_count: int
    masters: tuple[InstrumentMaster, ...]


@dataclass(frozen=True, slots=True)
class CollectionResult:

    correlation_id: str
    session_id: str
    summary: CollectorSummary


@dataclass(frozen=True, slots=True)
class AnalyticsResult:

    correlation_id: str
    trade_date: date
    outputs: ActorOutputs
    run_seconds: float


@dataclass(frozen=True, slots=True)
class ReconciliationResult:

    correlation_id: str
    trade_date: date
    breaches: tuple[GreekDiscrepancy, ...]

    @property
    def is_clean(self) -> bool:
        return not self.breaches


def refresh_universe(
    *,
    store: ParquetStore,
    config: PlatformConfig,
    masters: Sequence[InstrumentMaster],
    trade_date: date,
    correlation_id: str,
    persist: bool = True,
) -> UniverseRefreshResult:
    log = _LOGGER.bind(
        correlation_id=correlation_id,
        job="universe_refresh",
        trade_date=trade_date.isoformat(),
    )
    log.info("orchestration.universe_refresh.start", master_count=len(masters))
    if persist and masters:
        store.write("instrument_master", list(masters))
    log.info("orchestration.universe_refresh.done", master_count=len(masters))
    return UniverseRefreshResult(
        correlation_id=correlation_id,
        trade_date=trade_date,
        master_count=len(masters),
        masters=tuple(masters),
    )


FeedDriver = Callable[[RawCollector], None]


def collect_live(
    *,
    store: ParquetStore,
    adapter: MarketDataAdapter,
    subscribe: Sequence[str],
    session_id: str,
    trade_date: date,
    clock: Clock,
    drive: FeedDriver,
    correlation_id: str,
    metrics: OrchestrationMetrics | None = None,
) -> CollectionResult:
    log = _LOGGER.bind(
        correlation_id=correlation_id,
        job="collection",
        session_id=session_id,
        trade_date=trade_date.isoformat(),
    )
    log.info("orchestration.collection.start", subscribe_count=len(subscribe))
    collector = RawCollector(
        store=store,
        adapter=SequenceStamping(adapter),
        session_id=session_id,
        trade_date=trade_date,
        clock=clock,
        subscribed_keys=subscribe,
    )
    collector.start(list(subscribe))
    drive(collector)
    summary = collector.close()
    if metrics is not None:
        _record_collection_metrics(store, summary, trade_date, metrics)
    log.info(
        "orchestration.collection.done",
        event_count=summary.event_count,
        gap_count=summary.gap_count,
        reconnect_count=summary.reconnect_count,
        coverage_ratio=summary.coverage_ratio,
    )
    return CollectionResult(
        correlation_id=correlation_id, session_id=session_id, summary=summary
    )


def _record_collection_metrics(
    store: ParquetStore,
    summary: CollectorSummary,
    trade_date: date,
    metrics: OrchestrationMetrics,
) -> None:
    per_underlying: dict[str, int] = {}
    for event in replay_day(store, trade_date):
        if event.session_id == summary.session_id and is_observation(event.field_name):
            per_underlying[event.underlying] = per_underlying.get(event.underlying, 0) + 1
    for underlying, count in per_underlying.items():
        metrics.events_collected.labels(underlying=underlying).inc(count)


def run_incremental_analytics(
    *,
    store: ParquetStore,
    config: PlatformConfig,
    config_hashes: Mapping[str, str],
    positions: Sequence[Position],
    instruments: Sequence[InstrumentKey],
    masters: Sequence[InstrumentMaster],
    trade_date: date,
    as_of: datetime,
    calc_ts: datetime,
    clock: Clock,
    correlation_id: str,
    metrics: OrchestrationMetrics | None = None,
    persist: bool = True,
) -> AnalyticsResult:
    log = _LOGGER.bind(
        correlation_id=correlation_id,
        job="analytics",
        trade_date=trade_date.isoformat(),
    )
    events = replay_day(store, trade_date)
    log.info("orchestration.analytics.start", event_count=len(events))

    started = clock.now()
    outputs = run_analytics(
        events,
        positions,
        instruments=instruments,
        masters=masters,
        config=config,
        config_hashes=config_hashes,
        as_of=as_of,
        calc_ts=calc_ts,
    )
    run_seconds = (clock.now() - started).total_seconds()

    if metrics is not None:
        metrics.record_run_seconds("analytics", run_seconds)
        _record_analytics_metrics(
            events, outputs, masters, config, config_hashes, as_of, calc_ts, metrics
        )
    if persist:
        persist_outputs(store, outputs)

    log.info(
        "orchestration.analytics.done",
        run_seconds=run_seconds,
        snapshot_count=len(outputs.snapshots),
        forward_count=len(outputs.forwards),
        iv_point_count=len(outputs.iv_points),
        risk_aggregate_count=len(outputs.risk_aggregates),
        scenario_count=len(outputs.scenarios),
        persisted=persist,
    )
    return AnalyticsResult(
        correlation_id=correlation_id,
        trade_date=trade_date,
        outputs=outputs,
        run_seconds=run_seconds,
    )


def _record_analytics_metrics(
    events: Sequence[RawMarketEvent],
    outputs: ActorOutputs,
    masters: Sequence[InstrumentMaster],
    config: PlatformConfig,
    config_hashes: Mapping[str, str],
    as_of: datetime,
    calc_ts: datetime,
    metrics: OrchestrationMetrics,
) -> None:
    instrument_by_key = {master.instrument_key: master.instrument for master in masters}
    observations = tuple(event for event in events if is_observation(event.field_name))
    instruments = list(instrument_by_key.values())
    batch = build_snapshots(
        instruments,
        observations,
        snapshot_ts=as_of,
        qc=config.qc_threshold,
        calc_ts=calc_ts,
        config_hashes=config_hashes,
    )
    _record_stale_ratio(batch, metrics)
    _record_solver_failures(batch, outputs, instrument_by_key, metrics)


def _record_stale_ratio(batch: SnapshotBatch, metrics: OrchestrationMetrics) -> None:
    total: dict[str, int] = {}
    usable: dict[str, int] = {}
    for assessed in batch.assessed:
        underlying = assessed.snapshot.underlying
        total[underlying] = total.get(underlying, 0) + 1
        if assessed.assessment.is_usable:
            usable[underlying] = usable.get(underlying, 0) + 1
    for underlying, count in total.items():
        stale = count - usable.get(underlying, 0)
        metrics.stale_quote_ratio.labels(underlying=underlying).set(stale / count)


def _record_solver_failures(
    batch: SnapshotBatch,
    outputs: ActorOutputs,
    instrument_by_key: dict[str, InstrumentKey],
    metrics: OrchestrationMetrics,
) -> None:
    iv_underlyings: dict[str, int] = {}
    for point in outputs.iv_points:
        instrument = instrument_by_key.get(point.contract_key)
        if instrument is None:
            continue
        underlying = instrument.underlying_symbol
        iv_underlyings[underlying] = iv_underlyings.get(underlying, 0) + 1
    usable_options: dict[str, int] = {}
    for snapshot in batch.usable:
        instrument = instrument_by_key.get(snapshot.instrument_key)
        if instrument is None or not instrument.is_option():
            continue
        usable_options[instrument.underlying_symbol] = (
            usable_options.get(instrument.underlying_symbol, 0) + 1
        )
    for underlying, count in usable_options.items():
        missing = count - iv_underlyings.get(underlying, 0)
        if missing > 0:
            metrics.solver_failures.labels(underlying=underlying).inc(missing)


def record_forward_failure(
    metrics: OrchestrationMetrics, underlying: str, *, count: int = 1
) -> None:
    metrics.forward_failures.labels(underlying=underlying).inc(count)


def reconcile_end_of_day(
    *,
    lines: Sequence[PositionRisk],
    broker_greeks: Sequence[BrokerGreeks],
    trade_date: date,
    correlation_id: str,
) -> ReconciliationResult:
    log = _LOGGER.bind(
        correlation_id=correlation_id,
        job="reconciliation",
        trade_date=trade_date.isoformat(),
    )
    broker_by_key = {row.contract_key: row for row in broker_greeks}
    breaches: list[GreekDiscrepancy] = []
    for line in lines:
        broker = broker_by_key.get(line.contract_key)
        if broker is None:
            continue
        breaches.extend(reconcile(line, broker))
    log.info(
        "orchestration.reconciliation.done",
        line_count=len(lines),
        breach_count=len(breaches),
        breached_contracts=sorted({breach.contract_key for breach in breaches}),
    )
    return ReconciliationResult(
        correlation_id=correlation_id,
        trade_date=trade_date,
        breaches=tuple(breaches),
    )
