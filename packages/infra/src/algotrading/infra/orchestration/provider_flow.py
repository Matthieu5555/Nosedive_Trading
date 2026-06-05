"""Capture from several providers into one raw layer, through the one collector.

The multi-broker capture driver. Where a single ``collect_live`` captures one provider's feed,
``run_provider_flow`` captures *several* — Deribit, Saxo, IBKR — each through its own
:func:`collect_live` session into the *same* append-only raw layer, then runs the *single*
actor over the union. There is no second analytics path and no per-provider store: every
provider's ticks land as the one canonical ``RawMarketEvent`` (the source/leaf is recoverable
from the instrument key's provider segment, ADR 0017), so the actor downstream cannot tell one
broker from another. This is what ADR 0027 means by "Saxo/Deribit wire onto the runtime through
the unified collector."

Each provider is described by a :class:`ProviderCapture` — its adapter, the keys it covers, and
the callable that drives its feed to completion. The job runs them in order (a provider whose
capture raises is surfaced, not swallowed silently — a degraded provider must be visible), then
returns the per-provider collection summaries. Analytics over the captured union is the caller's
next step (``run_incremental_analytics`` / ``run_day``), kept separate so this job stays the
capture half and the one actor stays the only compute path.
"""

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
    """One provider's capture inputs: which adapter, what to subscribe, how to drive it.

    ``provider`` is the source/leaf label (DERIBIT/SAXO/IBKR) used for the session id and the
    trace. ``adapter`` is the broker's push :class:`~collectors.MarketDataAdapter`; ``subscribe``
    the canonical instrument keys it covers; ``drive`` the callable that pumps its feed to
    completion (a live async loop, a fake feed, or a replay source).
    """

    provider: str
    adapter: MarketDataAdapter
    subscribe: Sequence[str]
    drive: FeedDriver


@dataclass(frozen=True, slots=True)
class ProviderFlowResult:
    """What a multi-provider capture produced: one collection summary per provider."""

    correlation_id: str
    trade_date: date
    captures: tuple[CollectionResult, ...]

    @property
    def total_events(self) -> int:
        """Observations captured across every provider this flow ran."""
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
    """Capture every provider's feed into the one raw layer and return the per-provider summaries.

    Runs one :func:`collect_live` session per provider, each writing the one canonical
    ``RawMarketEvent`` shape into the shared store, all bound to ``correlation_id`` so the
    multi-provider capture is one resolvable trace. The session id is per-provider so each
    provider's events are attributable and a restart resumes the right session. Returns the
    collection summaries in provider order.
    """
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
