from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol, runtime_checkable

import structlog
from algotrading.core.config import PlatformConfig
from algotrading.infra.actor import ActorOutputs
from algotrading.infra.collectors import CollectorSummary, MarketDataAdapter
from algotrading.infra.connectivity import (
    Clock,
    FeedNotice,
    MarketDataStatus,
    assess_market_data,
    classify_feed_notice,
)
from algotrading.infra.connectivity.market_data_policy import UNKNOWN
from algotrading.infra.contracts import InstrumentKey, InstrumentMaster
from algotrading.infra.storage import ParquetStore
from algotrading.infra.surfaces import SurfaceSliceSummary, summarize_surface_parameters

from .jobs import FeedDriver, collect_live, run_incremental_analytics

_LOGGER = structlog.get_logger("orchestration")


@runtime_checkable
class MarketDataDiagnostics(Protocol):

    @property
    def requested_market_data_type(self) -> int: ...

    @property
    def observed_market_data_type(self) -> int: ...

    def feed_errors(self) -> tuple[tuple[int, str], ...]: ...


@dataclass(frozen=True, slots=True)
class SurfaceJobRequest:

    symbol: str
    trade_date: date
    market_data_type: int
    as_of: datetime | None = None
    calc_ts: datetime | None = None
    persist: bool = True


@dataclass(frozen=True, slots=True)
class SurfaceJobResult:

    correlation_id: str
    request: SurfaceJobRequest
    outputs: ActorOutputs
    collection: CollectorSummary
    market_data_status: MarketDataStatus
    slices: tuple[SurfaceSliceSummary, ...]

    @property
    def fitted_maturities(self) -> int:
        return len(self.slices)


def build_surface(
    *,
    request: SurfaceJobRequest,
    store: ParquetStore,
    config: PlatformConfig,
    config_hashes: Mapping[str, str],
    adapter: MarketDataAdapter,
    masters: list[InstrumentMaster],
    drive: FeedDriver,
    clock: Clock,
    correlation_id: str,
    diagnostics: MarketDataDiagnostics | None = None,
) -> SurfaceJobResult:
    log = _LOGGER.bind(
        correlation_id=correlation_id,
        job="surface",
        symbol=request.symbol,
        trade_date=request.trade_date.isoformat(),
    )
    log.info("orchestration.surface.start")

    instruments: list[InstrumentKey] = [master.instrument for master in masters]
    subscribe = [instrument.canonical() for instrument in instruments]
    log.info("orchestration.surface.universe", contract_count=len(subscribe))

    session_id = f"surface-{request.symbol}-{request.trade_date.isoformat()}"
    collection = collect_live(
        store=store,
        adapter=adapter,
        subscribe=subscribe,
        session_id=session_id,
        trade_date=request.trade_date,
        clock=clock,
        drive=drive,
        correlation_id=correlation_id,
    )
    summary = collection.summary

    status = _assess_feed(request, summary, diagnostics, clock)

    as_of = request.as_of if request.as_of is not None else clock.now()
    calc_ts = request.calc_ts if request.calc_ts is not None else as_of
    analytics = run_incremental_analytics(
        store=store,
        config=config,
        config_hashes=config_hashes,
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
