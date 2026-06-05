"""The operable jobs — each a function of injected dependencies and a correlation id.

A job here is one unit of operable work: universe refresh, live collection, incremental
analytics, and end-of-day reconciliation. Every job takes its dependencies (the store,
the config, a clock, the metric bundle) as parameters rather than constructing them, so a
test drives a job with fakes and an injected clock and nothing reaches a real broker or a
wall clock. Every job emits a structured log line bound to a ``correlation_id``, and
that same id is threaded from the collector session through the analytics run, so a
single trace resolves a session to the jobs it fed — the actor already binds the id
onto its own log lines, and these jobs propagate it.

Each job returns a small frozen result describing what it did (counts, status, the
correlation id) so the pipeline can record it and the dashboard can read it. A job
does not schedule itself: it is a plain function the pipeline (or a scheduler, or a
test) calls directly.

Live collection rides the one unified collection seam (ADR 0027): :func:`collect_live`
drives a broker adapter through the single :class:`collectors.RawCollector`, which writes
content-addressed ``RawMarketEvent`` rows — the *same* collector and event shape the
replay path uses, so live capture is exactly-once and live==replay holds. The adapter is
injected and the feed is driven by an injected callable, so a test runs the job over a
fake feed (or a replay source) with no broker and no second code path.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
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
    """What a universe-refresh job produced: the masters materialized for the day."""

    correlation_id: str
    trade_date: date
    master_count: int
    masters: tuple[InstrumentMaster, ...]


@dataclass(frozen=True, slots=True)
class CollectionResult:
    """What a collection job captured, as the collector's own daily summary.

    The result type of :func:`collect_live` and of the EOD pipeline's collection stage.
    """

    correlation_id: str
    session_id: str
    summary: CollectorSummary


@dataclass(frozen=True, slots=True)
class AnalyticsResult:
    """What an incremental-analytics (run_day) job derived for one as-of instant."""

    correlation_id: str
    trade_date: date
    outputs: ActorOutputs
    run_seconds: float


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    """The Greek breaches an end-of-day reconciliation surfaced against the broker."""

    correlation_id: str
    trade_date: date
    breaches: tuple[GreekDiscrepancy, ...]

    @property
    def is_clean(self) -> bool:
        """True when every reconciled line agreed with the broker within tolerance."""
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
    """Materialize the day's instrument masters and persist them to the raw layer.

    The universe plane owns the resolution of a broker chain into masters; this job
    orchestrates persisting the resolved masters for the trade date and emitting the
    structured trace. The masters are passed in already resolved (dependency
    injection — the resolver, like the broker, is a caller-supplied input), written
    through A's append-only ``instrument_master`` table, which is idempotent on the
    instrument key, so a re-run of the refresh re-asserts the same masters rather than
    duplicating them. Returns the masters so the analytics job downstream uses the
    exact set this refresh published.
    """
    log = _LOGGER.bind(
        correlation_id=correlation_id,
        job="universe_refresh",
        trade_date=trade_date.isoformat(),
        underlyings=list(config.universe.underlyings),
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


# A driver pumps a wired collector's feed to completion: it subscribes the adapter, drives
# the stream (a live async WS loop; a fake feed; or a replay source's pump), and surfaces any
# reconnect to the collector. It is injected so the job is broker-agnostic and testable.
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
    """Capture one collection session through the one unified collector and record its metric.

    Wraps the injected push ``adapter`` with :class:`collectors.SequenceStamping` (so every
    tick gets the stable per-(instrument, field) ordinal the content-addressed id needs),
    builds the single :class:`collectors.RawCollector` over the store, subscribes, and hands
    the collector to the injected ``drive`` callable that pumps the feed to completion. The
    ``session_id`` is the correlation handle: it is stable across restarts (so a restart
    resumes the same session, not a fresh one, and the collector reloads its already-written
    ids) and it is the id the downstream analytics run carries, which links a session to the
    jobs it fed. Bumps the ``events_collected_total`` counter by the observations captured,
    labeled by underlying. Returns the collector's daily summary.
    """
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
    """Increment the per-underlying event counter from a session's persisted events.

    Reads the day's observations back off the raw layer and counts them per underlying, so the
    ``events_collected_total`` counter is labeled by underlying rather than lumped into one
    opaque total. Gap meta-events are not observations and are not counted.
    """
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
    config_hash: str,
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
    """Replay the day's raw events through the actor and record the analytics metrics.

    This is the orchestration wrapper around ``actor.run_day``'s compute: it replays
    the stored raw events for the date, runs ``actor.run_analytics`` over them (one
    code path with live), times the run against the injected ``clock`` and observes it
    on the ``scenario_run_seconds`` histogram, derives the stale-quote ratio and the
    forward/solver failure counts from the same events, and persists. The
    ``correlation_id`` is the collector session's id, so the actor's
    ``actor.run_day.*`` log lines and this job's lines share it — the end-to-end
    trace. Returns the :class:`actor.ActorOutputs` plus the measured run time.

    ``clock`` is any object with a ``now() -> datetime`` (the injected
    :class:`connectivity.Clock`); the run is timed against it so nothing here reads a
    wall clock.
    """
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
        config_hash=config_hash,
        as_of=as_of,
        calc_ts=calc_ts,
    )
    run_seconds = (clock.now() - started).total_seconds()

    if metrics is not None:
        metrics.record_run_seconds("analytics", run_seconds)
        _record_analytics_metrics(
            events, outputs, masters, config, config_hash, as_of, calc_ts, metrics
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
    config_hash: str,
    as_of: datetime,
    calc_ts: datetime,
    metrics: OrchestrationMetrics,
) -> None:
    """Derive the stale-quote and solver-failure metrics for a run.

    Rebuilds the snapshot batch the actor built (the same pure ``build_snapshots`` over
    the same observed events) to read the usable/total split per underlying for the
    stale-quote gauge. A solver failure is a usable option quote that produced no IV
    point — the actor drops an unconverged solve. Both are counted per underlying using
    each instrument's master (the key→instrument map the job was already handed) rather
    than parsing the canonical key, so the counters move on exactly the events the spec
    names. The forward-failure counter is fed separately, where the per-maturity failure
    is actually observed (see :func:`record_forward_failure`).
    """
    instrument_by_key = {master.instrument_key: master.instrument for master in masters}
    observations = tuple(event for event in events if is_observation(event.field_name))
    instruments = list(instrument_by_key.values())
    batch = build_snapshots(
        instruments,
        observations,
        snapshot_ts=as_of,
        qc=config.qc_threshold,
        calc_ts=calc_ts,
        config_hash=config_hash,
    )
    _record_stale_ratio(batch, metrics)
    _record_solver_failures(batch, outputs, instrument_by_key, metrics)


def _record_stale_ratio(batch: SnapshotBatch, metrics: OrchestrationMetrics) -> None:
    """Set the stale-quote gauge per underlying from the snapshot batch's verdicts."""
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
    """Bump the solver-failure counter per underlying: usable option quotes with no IV.

    A usable option snapshot that did not yield an IV point is a solver non-convergence.
    Counting the gap between usable option quotes and emitted IV points per underlying
    gives the solver-failure count without re-running the solver here. Underlyings come
    from the instrument master, not from parsing the canonical key.
    """
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
    """Bump the forward-failure counter for an underlying by ``count``.

    Exposed as a named entry point because a forward failure is detected where the
    forward is built (a maturity with no usable call/put pair), which the actor
    swallows internally; the reconstruction/replay layer that does see the per-maturity
    failure calls this to register it. Kept here so the one counter has one mutator.
    """
    metrics.forward_failures.labels(underlying=underlying).inc(count)


def reconcile_end_of_day(
    *,
    lines: Sequence[PositionRisk],
    broker_greeks: Sequence[BrokerGreeks],
    trade_date: date,
    correlation_id: str,
) -> ReconciliationResult:
    """Reconcile computed risk lines against broker Greeks and surface the breaches.

    The end-of-day risk-vs-broker check: for each line that has a matching broker row
    (joined on contract key), run D's :func:`risk.reconcile` and collect every Greek
    that disagreed beyond tolerance. A line with no broker row is skipped (the broker
    did not return it — not a disagreement). The result is clean when nothing breached;
    the breaches name the exact contract and Greek, so an operator sees which position
    to investigate. Pure over its inputs — no store, no clock.
    """
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
