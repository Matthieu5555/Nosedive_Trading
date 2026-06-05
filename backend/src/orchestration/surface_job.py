"""Build a volatility surface for one underlying — the reusable use case.

The whole "give me a surface for this symbol" workflow, behind one function of injected
dependencies: resolve the option chain off a broker session, collect a window of quotes
into the immutable raw layer, run the *exact* actor pipeline production runs
(``snapshots -> forwards -> IV -> SVI surface``), assess the feed's entitlement/health, and
reduce the fitted surface to summary rows. It composes the existing collection and
analytics jobs rather than reimplementing them, so a live run, a scheduled job, a replay,
or an API endpoint all reach a surface through this one path instead of copying the
script.

It owns no math and no broker specifics: the chain *policy* lives in
:mod:`universe.chain_planning`, the surface math in :mod:`surfaces`, and the broker session
is injected (any :class:`~connectivity.BrokerSession`). Entitlement diagnostics are read
from an optional :class:`MarketDataDiagnostics` source — the live IBKR adapter supplies
them; a fake or replay session does not, and the status then simply reports the
subscribed/producing counts. Nothing here reads a clock for the compute: ``as_of`` and
``calc_ts`` are carried on the request, so the analytics are reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol, runtime_checkable

import structlog

from actor import ActorOutputs
from collectors import CollectorSummary
from config import PlatformConfig
from connectivity import (
    Clock,
    FeedNotice,
    MarketDataStatus,
    SessionSupervisor,
    assess_market_data,
    classify_feed_notice,
)
from connectivity.market_data_policy import UNKNOWN
from storage import ParquetStore
from surfaces import SurfaceSliceSummary, summarize_surface_parameters
from universe import ChainSelection, UniverseService, materialize_universe

from .jobs import collect_live, run_incremental_analytics

_LOGGER = structlog.get_logger("orchestration")


@runtime_checkable
class MarketDataDiagnostics(Protocol):
    """A session that can report its market-data entitlement state after a run.

    The live IBKR adapter satisfies this; the broker-agnostic fakes do not, which is why
    :func:`build_surface` takes it as an optional, separate input rather than reading it off
    the :class:`~connectivity.BrokerSession` it drives.
    """

    @property
    def requested_market_data_type(self) -> int: ...

    @property
    def observed_market_data_type(self) -> int: ...

    def feed_errors(self) -> tuple[tuple[int, str], ...]: ...


@dataclass(frozen=True, slots=True)
class SurfaceJobRequest:
    """What surface to build, and the as-of it is valued at.

    ``selection`` bounds the option chain (see :class:`~universe.ChainSelection`);
    ``market_data_type`` is the feed type the live session requested (recorded on the
    status). ``as_of`` is the valuation/snapshot instant and ``calc_ts`` the computation
    stamp. Leave them ``None`` for a live run — the job stamps them from the clock *after*
    collection, so the snapshot never values as-of a time before the quotes it read (no
    look-ahead). Pass them explicitly to reproduce a specific instant (tests, replay).
    """

    symbol: str
    trade_date: date
    selection: ChainSelection
    market_data_type: int
    as_of: datetime | None = None
    calc_ts: datetime | None = None
    persist: bool = True


@dataclass(frozen=True, slots=True)
class SurfaceJobResult:
    """What a surface build produced: outputs, collection, feed status, and the summary."""

    correlation_id: str
    request: SurfaceJobRequest
    outputs: ActorOutputs
    collection: CollectorSummary
    market_data_status: MarketDataStatus
    slices: tuple[SurfaceSliceSummary, ...]

    @property
    def fitted_maturities(self) -> int:
        """How many maturities produced a calibrated SVI smile."""
        return len(self.slices)


def build_surface(
    *,
    request: SurfaceJobRequest,
    store: ParquetStore,
    config: PlatformConfig,
    config_hash: str,
    supervisor: SessionSupervisor,
    clock: Clock,
    correlation_id: str,
    diagnostics: MarketDataDiagnostics | None = None,
) -> SurfaceJobResult:
    """Resolve a chain, collect quotes, run the actor, and summarize the fitted surface.

    The composed use case. It resolves and materializes the bounded chain, subscribes every
    resolved contract, drives :func:`~orchestration.collect_live` over the session, builds a
    :class:`~connectivity.MarketDataStatus` from the feed diagnostics and the collection
    counts, runs :func:`~orchestration.run_incremental_analytics` (positions empty — a
    surface needs no book) over the freshly-collected raw events, and reduces the persisted
    SVI parameters to summary rows. Every step shares ``correlation_id`` so one trace links
    the session to the surface it produced. Returns the outputs, the collection summary, the
    feed status, and the per-maturity summaries.
    """
    log = _LOGGER.bind(
        correlation_id=correlation_id,
        job="surface",
        symbol=request.symbol,
        trade_date=request.trade_date.isoformat(),
    )
    log.info("orchestration.surface.start")

    rows = supervisor.request_option_chain(request.symbol)
    masters = materialize_universe(store, rows, request.trade_date)
    instruments = [master.instrument for master in masters]
    universe = UniverseService(instruments, request.trade_date)
    con_ids = [instrument.broker_contract_id for instrument in instruments]
    log.info("orchestration.surface.universe", contract_count=len(con_ids))

    session_id = f"surface-{request.symbol}-{request.trade_date.isoformat()}"
    collection = collect_live(
        store=store,
        universe=universe,
        supervisor=supervisor,
        subscribe=con_ids,
        session_id=session_id,
        trade_date=request.trade_date,
        clock=clock,
        correlation_id=correlation_id,
    )
    summary = collection.summary

    status = _assess_feed(request, summary, diagnostics, clock)

    # Value as-of a time no earlier than the quotes just collected: an injected instant
    # when given (reproducible), else the clock now that collection has finished, so the
    # snapshot never reads a quote from after its own as-of.
    as_of = request.as_of if request.as_of is not None else clock.now()
    calc_ts = request.calc_ts if request.calc_ts is not None else as_of
    analytics = run_incremental_analytics(
        store=store,
        config=config,
        config_hash=config_hash,
        positions=[],
        instruments=instruments,
        masters=masters,
        trade_date=request.trade_date,
        as_of=as_of,
        calc_ts=calc_ts,
        clock=clock,
        correlation_id=correlation_id,
        persist=request.persist,
    )
    slices = summarize_surface_parameters(analytics.outputs.surface_parameters)

    log.info(
        "orchestration.surface.done",
        event_count=summary.event_count,
        fitted_maturities=len(slices),
        producing=status.producing,
        has_entitlement_problem=status.has_entitlement_problem,
    )
    return SurfaceJobResult(
        correlation_id=correlation_id,
        request=request,
        outputs=analytics.outputs,
        collection=summary,
        market_data_status=status,
        slices=slices,
    )


def _assess_feed(
    request: SurfaceJobRequest,
    summary: CollectorSummary,
    diagnostics: MarketDataDiagnostics | None,
    clock: Clock,
) -> MarketDataStatus:
    """Build the feed status from the session diagnostics and the collection counts.

    When a session reports diagnostics (the live adapter), its raw error notices are
    classified with the injected clock and its observed market-data type is read off; a
    fake/replay session reports none, so the status carries ``UNKNOWN`` types and no notices
    but still records whether anything subscribed actually produced.
    """
    notices: tuple[FeedNotice, ...]
    if diagnostics is None:
        requested, effective = request.market_data_type, UNKNOWN
        notices = ()
    else:
        requested = diagnostics.requested_market_data_type
        effective = diagnostics.observed_market_data_type
        notices = tuple(
            classify_feed_notice(code, message, clock.now())
            for code, message in diagnostics.feed_errors()
        )
    return assess_market_data(
        requested_type=requested,
        effective_type=effective,
        subscribed=summary.subscribed_count,
        producing=summary.covered_count,
        notices=notices,
    )
