"""Daily close-snapshot capture mode (roadmap WS 1C, Part B).

The platform's live/recent capture path (``run_analytics``/``run_day``) records the market as
it streams. The close-snapshot mode adds the *other* daily product: one immutable,
provenance-stamped snapshot set per trade date, taken at the **session's close** for the index
and every constituent — the :class:`MarketStateSnapshot` rows the rest of Phase 1 builds on.

It reuses the existing pure path rather than forking it (the 1C spec is explicit about this):

* ``run_analytics`` already accepts ``session_open`` — close mode passes ``session_open=False``
  ("session closed, reference = close"). The reference-spot ladder
  (:mod:`snapshots.reference_spot`) already resolves a ``close`` rung, look-ahead-guarded.
* The injected ``as_of`` is **that index's own** ``session_close(index, trade_date)`` from the
  1J calendar resolver — Eurex close for SX5E, NYSE close for SPX — never a wall clock and
  never a single global close. A multi-exchange run captures each index at its own close.
* ``calc_ts`` is the same close instant, so nothing reads a clock and the close set is
  byte-identical on replay (the property four other specs depend on).

The index list comes from the 1J registry's :func:`enabled_indices` — never hardcoded; a
disabled index is simply absent. Each enabled index's basket (instruments, masters, positions)
is caller-supplied per index (1A owns the basket, 1B the selected contracts; 1C only provides
the mode), the same dependency-injection stance the rest of the actor takes.

Persistence is replace-idempotent: ``persist_outputs`` writes the derived partitions for the
``(provider, trade_date)`` and re-running the same day replaces exactly those partitions,
never touching the append-only raw layer and never duplicating the set.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime

import structlog
from algotrading.core.config import PlatformConfig
from algotrading.infra.contracts import (
    InstrumentKey,
    InstrumentMaster,
    Position,
    RawMarketEvent,
)
from algotrading.infra.storage import ParquetStore
from algotrading.infra.universe import CalendarResolver, IndexEntry, enabled_indices
from algotrading.infra.universe.index_registry import IndexRegistry

from .driver import persist_outputs, run_analytics
from .outputs import ActorOutputs
from .valuation_join import default_exercise_style

_LOGGER = structlog.get_logger("actor.close")


@dataclass(frozen=True, slots=True)
class IndexBasket:
    """The point-in-time basket to capture at close for one index (1A × 1B).

    ``instruments`` is the index plus its selected constituent contracts; ``events`` the
    close-session market observations for them (the raw quotes at the close); ``masters`` the
    matching instrument masters; ``positions`` any held positions to value at close (empty for
    a pure market-state capture). All caller-supplied per index — 1C does not resolve the
    basket, it captures it. Determinism: the snapshot set is a pure function of these inputs and
    the injected close instant, so feeding the same close events twice (in any order) yields a
    byte-identical set.
    """

    instruments: tuple[InstrumentKey, ...]
    events: tuple[RawMarketEvent, ...]
    masters: tuple[InstrumentMaster, ...]
    positions: tuple[Position, ...] = ()


@dataclass(frozen=True, slots=True)
class CloseCaptureResult:
    """What a close capture produced for one index: its close instant and the outputs."""

    index: str
    session_close: datetime
    outputs: ActorOutputs


# The default source label the close grid's provider-partitioned cells are stamped with. The
# index registry's only provider sub-block today is `ibkr:` (ADR 0035), so the daily close set
# is captured off IBKR; a future Saxo/Deribit sibling passes its own label through `provider`.
DEFAULT_PROVIDER = "IBKR"


def capture_index_close(
    *,
    index: IndexEntry,
    basket: IndexBasket,
    resolver: CalendarResolver,
    trade_date: date,
    config: PlatformConfig,
    config_hashes: Mapping[str, str],
    exercise_style_for: Callable[[InstrumentKey], str] = default_exercise_style,
    store: ParquetStore | None = None,
    provider: str = DEFAULT_PROVIDER,
) -> CloseCaptureResult:
    """Capture one index's close-snapshot set at its own session close.

    Resolves ``as_of = resolver.session_close(index.symbol, trade_date)`` — the index's own,
    timezone-correct close instant (a non-session date raises a labeled
    ``CalendarResolutionError`` from the resolver, never a guessed instant) — and runs
    ``run_analytics`` over the basket with ``session_open=False`` and ``calc_ts`` equal to that
    close instant. ``provider`` stamps the provider-partitioned grid, so the close run also
    produces and persists the pinned tenor × delta-band :class:`ProjectedOptionAnalytics` grid
    (WS 1F) — this is the live path that reaches :func:`surfaces.project_grid`. When ``store``
    is given the outputs (snapshots, surfaces, *and* the grid) are replace-persisted (idempotent
    for the day). Pure given a fixed ``trade_date``/basket/config: no wall clock is read, so the
    set is byte-identical on a re-run.
    """
    as_of = resolver.session_close(index.symbol, trade_date)
    outputs = run_analytics(
        basket.events,
        basket.positions,
        instruments=basket.instruments,
        masters=basket.masters,
        config=config,
        config_hashes=config_hashes,
        as_of=as_of,
        calc_ts=as_of,
        exercise_style_for=exercise_style_for,
        session_open=False,
        provider=provider,
    )
    if store is not None:
        persist_outputs(store, outputs)
    _LOGGER.info(
        "actor.close.captured",
        index=index.symbol,
        provider=provider,
        trade_date=trade_date.isoformat(),
        session_close=as_of.isoformat(),
        snapshot_count=len(outputs.snapshots),
        projected_cell_count=len(outputs.projected_analytics),
    )
    return CloseCaptureResult(index=index.symbol, session_close=as_of, outputs=outputs)


def capture_daily_close(
    *,
    registry: IndexRegistry,
    baskets: Mapping[str, IndexBasket],
    resolver: CalendarResolver,
    trade_date: date,
    config: PlatformConfig,
    config_hashes: Mapping[str, str],
    exercise_style_for: Callable[[InstrumentKey], str] = default_exercise_style,
    store: ParquetStore | None = None,
    provider: str = DEFAULT_PROVIDER,
) -> tuple[CloseCaptureResult, ...]:
    """Capture the close-snapshot set for every enabled index, each at its own close.

    Iterates :func:`enabled_indices` (never a hardcoded list — a disabled index is absent), and
    for each captures its basket at that index's ``session_close``. An index with no basket
    supplied is skipped (nothing to capture), not an error. Returns one
    :class:`CloseCaptureResult` per captured index, in canonical (sorted-symbol) order. This is
    the callable the EOD collection/analytics stage invokes; 1G owns *when* it fires, 1C only
    provides the mode.
    """
    results: list[CloseCaptureResult] = []
    for index in enabled_indices(registry):
        basket = baskets.get(index.symbol)
        if basket is None:
            continue
        results.append(
            capture_index_close(
                index=index,
                basket=basket,
                resolver=resolver,
                trade_date=trade_date,
                config=config,
                config_hashes=config_hashes,
                exercise_style_for=exercise_style_for,
                store=store,
                provider=provider,
            )
        )
    return tuple(results)


def make_close_capture(
    *,
    registry: IndexRegistry,
    resolver: CalendarResolver,
    config: PlatformConfig,
    config_hashes: Mapping[str, str],
    store: ParquetStore,
    exercise_style_for: Callable[[InstrumentKey], str] = default_exercise_style,
    provider: str = DEFAULT_PROVIDER,
) -> Callable[[date, Mapping[str, IndexBasket]], tuple[CloseCaptureResult, ...]]:
    """Bind the close-capture dependencies into a ``(trade_date, baskets) -> results`` callable.

    The seam the EOD stage / cron (1G) wires: it hands back a callable that captures the daily
    close set for the enabled indices, with the store/registry/resolver/config already bound.
    Keeps 1C's mode injectable as one function while leaving the schedule to 1G.
    """

    def _capture(
        trade_date: date, baskets: Mapping[str, IndexBasket]
    ) -> tuple[CloseCaptureResult, ...]:
        return capture_daily_close(
            registry=registry,
            baskets=baskets,
            resolver=resolver,
            trade_date=trade_date,
            config=config,
            config_hashes=config_hashes,
            exercise_style_for=exercise_style_for,
            store=store,
            provider=provider,
        )

    return _capture
