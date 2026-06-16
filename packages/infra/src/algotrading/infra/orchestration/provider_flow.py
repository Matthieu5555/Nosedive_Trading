from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import structlog
from algotrading.infra.collectors import MarketDataAdapter
from algotrading.infra.connectivity import Clock
from algotrading.infra.storage import ParquetStore

from .jobs import CollectionResult, FeedDriver, collect_live
from .metrics import OrchestrationMetrics

_LOGGER = structlog.get_logger("orchestration")


@dataclass(frozen=True, slots=True)
class ProviderCapture:

    provider: str
    adapter: MarketDataAdapter
    subscribe: Sequence[str]
    drive: FeedDriver


@dataclass(frozen=True, slots=True)
class ProviderFlowResult:

    correlation_id: str
    trade_date: date
    captures: tuple[CollectionResult, ...]

    @property
    def total_events(self) -> int:
        return sum(capture.summary.event_count for capture in self.captures)


def run_provider_flow(
    *,
    store: ParquetStore,
    providers: Sequence[ProviderCapture],
    trade_date: date,
    clock: Clock,
    correlation_id: str,
    metrics: OrchestrationMetrics | None = None,
) -> ProviderFlowResult:
    log = _LOGGER.bind(
        correlation_id=correlation_id,
        job="provider_flow",
        trade_date=trade_date.isoformat(),
        providers=[capture.provider for capture in providers],
    )
    log.info("orchestration.provider_flow.start", provider_count=len(providers))
    captures: list[CollectionResult] = []
    for capture in providers:
        session_id = f"{capture.provider.lower()}-{trade_date.isoformat()}"
        result = collect_live(
            store=store,
            adapter=capture.adapter,
            subscribe=capture.subscribe,
            session_id=session_id,
            trade_date=trade_date,
            clock=clock,
            drive=capture.drive,
            correlation_id=correlation_id,
            metrics=metrics,
        )
        captures.append(result)
    log.info(
        "orchestration.provider_flow.done",
        total_events=sum(c.summary.event_count for c in captures),
    )
    return ProviderFlowResult(
        correlation_id=correlation_id,
        trade_date=trade_date,
        captures=tuple(captures),
    )
