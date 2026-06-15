"""The research backtester (TARGET §5.7 / §7.8) — does this idea have edge?

This is the **research** machine of the two §5.7 asks: replay a strategy over banked history
day by day and answer "does this idea have edge?" with the serious output — performance,
drawdowns, turnover, exposure, Greeks, stress losses, and *attribution through time*. The
**production-shadow** machine ("would my live system have produced this P&L?") is the deliberate
second build and is **not** here (see the README's scope note); this engine keeps it cheap by
calling the strategy through the *same* :func:`~algotrading.strategy.run_strategy` convention
paper/live use, so the two contexts cannot diverge in how the strategy is invoked.

**It reinvents nothing.** The substrate the spec calls "genuinely ready" does the work:

* the strategy is driven through the landed §6 four-context harness
  (:func:`run_strategy`, ``context=BACKTEST``), the same call paper/live make;
* each day's book is priced into landed :class:`PositionRisk` lines by :class:`BacktestBook`;
* the day-over-day P&L is decomposed by the landed
  :func:`~algotrading.infra.risk.attribution.attribute_realized_book` — the realized per-Greek
  attribution engine, the §5.7 "attribution through time" primitive, already built;
* the stress loss is the landed
  :func:`~algotrading.infra.risk.scenarios.worst_case` over the same lines.

The engine is the loop and the bookkeeping between those: advance the date, read as-of state
through the :class:`BacktestData` seam, run the strategy, apply the entry, mark, attribute,
stress, and record one :class:`DayResult`. **No look-ahead, by construction:** every read is
keyed to the loop's current ``as_of``; the day's decision and mark are functions of state at or
before it; the day-over-day attribution reads *yesterday's* book as its start-of-day anchor and
*today's* market as the end — never a future bar.

**The first concrete target (§7.8):** S2, the index short-put line, replayed through a banked
stretch and an adverse regime (the course's 2021-vs-2008 method, p.129-130). S2 is config-only
and sells one put a day gated by ``decide_sell`` (signal ∧ capacity); the engine drives exactly
that — :meth:`PutLineStrategy.decide_sell` for the daily add, the harness for the protocol
decisions, the book for the rolling roll-off.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from algotrading.infra.contracts import Basket
from algotrading.infra.risk.attribution import (
    RealizedBookAttribution,
    attribute_realized_book,
)
from algotrading.infra.risk.config import AttributionConfig
from algotrading.infra.risk.scenarios import Scenario, scenario_line_pnls, worst_case

from ..harness import StrategyContext, run_strategy
from ..s2_put_line import PutLineStrategy
from ..signals import SignalSnapshot
from ..strategy import EntryAction, MarketState, Strategy
from .book import BacktestBook, PricedBook
from .data import BacktestData, HeldContract
from .results import (
    BacktestResult,
    DayGreeks,
    DayResult,
    summarise,
)


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    """The injected configuration of one backtest run — DI, never ``.py`` literals (ADR 0028).

    * ``basket_id_prefix`` — the stamp each day's constructed basket carries (the harness asserts
      the strategy's own ``strategy_id`` on it); the day is appended for a unique per-day id.
    * ``attribution`` — the landed :class:`AttributionConfig` (gamma normalisation / theta day
      count / residual tolerances) the day-over-day decomposition runs under, so the backtest's
      attribution matches the live book's exactly.
    * ``stress_grid`` — the scenario grid the daily stress loss is the worst case over (the
      caller builds it from the platform scenario config via ``scenario_grid``); an **empty** grid
      means "no stress column" and every day's ``stress_loss`` is ``0.0`` rather than an error.
    """

    basket_id_prefix: str
    attribution: AttributionConfig
    stress_grid: tuple[Scenario, ...]


def _book_greeks(priced: PricedBook) -> DayGreeks:
    """Sum the priced lines into the book's close-of-day dollar Greeks (the exposure line)."""
    return DayGreeks(
        delta=sum(line.position_delta for line in priced.lines),
        gamma=sum(line.position_gamma for line in priced.lines),
        vega=sum(line.position_vega for line in priced.lines),
        theta=sum(line.position_theta for line in priced.lines),
    )


def _stress_loss(priced: PricedBook, grid: tuple[Scenario, ...]) -> float:
    """The worst-case full-reprice loss over ``grid`` on the day's lines (``0.0`` if no risk).

    Reuses the landed :func:`scenario_line_pnls` + :func:`worst_case` — the same stress engine
    the live risk view runs — over the book's priced lines. A flat book or an empty grid has no
    worst case to take, so the stress loss is ``0.0`` (no position can lose under no scenario).
    The result is the worst scenario's total P&L (a loss is negative); ``min`` with ``0.0`` keeps
    it a *loss* column, never a positive (a grid where every scenario is a gain still reports 0).
    """
    if not priced.lines or not grid:
        return 0.0
    cells = scenario_line_pnls(priced.lines, grid)
    return min(0.0, worst_case(cells).total_pnl)


def _attribute_day(
    prior: PricedBook | None,
    today: PricedBook,
    config: AttributionConfig,
) -> RealizedBookAttribution | None:
    """Decompose the day-over-day P&L of the book carried *into* the day (or ``None``).

    The realized attribution reads *yesterday's* book as the start-of-day lines (the look-ahead
    anchor — Greeks known before the move) and re-marks each of those contracts at *today's*
    market, so the P&L is the move on the book that was actually held overnight. A line that was
    held yesterday but cannot be marked today (expired / data-gapped) is dropped from the move
    rather than attributed against a missing end — so the start set is intersected with today's
    valuations. Returns ``None`` when there was no prior book (the first day) or nothing carried
    over survived to today (a fully-rolled or fully-gapped book): there is no day-over-day move to
    decompose, not a zero one.
    """
    if prior is None or not prior.lines:
        return None
    starts = [line for line in prior.lines if line.contract_key in today.valuations]
    if not starts:
        return None
    ends = {key: today.valuations[key] for key in (line.contract_key for line in starts)}
    return attribute_realized_book(starts, ends, config)


def run_backtest(
    strategy: Strategy,
    data: BacktestData,
    *,
    dates: Sequence[date],
    config: BacktestConfig,
) -> BacktestResult:
    """Replay ``strategy`` over ``dates`` against the as-of ``data`` seam — the research backtest.

    Walks the dates in order, holding one mutable :class:`BacktestBook`. For each day:

    1. **Roll off** contracts that expired on or before the day (S2's daily put roll-off).
    2. **Price** the book at the day's market into landed :class:`PositionRisk` lines.
    3. **Attribute** the day-over-day P&L of the book carried in (yesterday's lines re-marked at
       today's market) with the landed realized attribution — the through-time view.
    4. **Run the strategy** through the §6 harness (``context=BACKTEST``) on the as-of signals and
       the day's :class:`MarketState`, and — for a strategy with a capacity-gated daily decision
       like S2 — through :meth:`PutLineStrategy.decide_sell` so the line's capacity cap is honoured.
    5. **Open** the constructed legs (if entry fired) into the book, concretized to fixed
       contracts through the data seam.
    6. **Record** the day's :class:`DayResult` (P&L, exposure Greeks, attribution, stress loss).

    Every read is keyed to the loop's ``as_of``; no step reads a future date, so the replay is
    look-ahead-free by construction (the cardinal backtester rule). Pure of wall-clock: the date
    is the loop variable, never ``date.today()``. Returns the full :class:`BacktestResult` — the
    day path plus the summary rolled up from it.
    """
    book = BacktestBook()
    prior_priced: PricedBook | None = None
    cumulative_pnl = 0.0
    day_results: list[DayResult] = []

    for as_of in dates:
        book.expire(as_of)

        priced = book.price(data, as_of)
        attribution = _attribute_day(prior_priced, priced, config.attribution)
        realized_pnl = attribution.full_reprice_pnl if attribution is not None else None
        if realized_pnl is not None:
            cumulative_pnl += realized_pnl

        entered = _run_day(strategy, data, book, as_of, config)

        # Re-price after the day's add so the recorded exposure/stress reflect the book actually
        # carried into the *next* day (the new put is part of tonight's risk). The attribution
        # above is on the book held *into* today — these two prices are deliberately different
        # snapshots of the book (start-of-day for the move, end-of-day for the exposure).
        closed = book.price(data, as_of)

        day_results.append(
            DayResult(
                as_of=as_of,
                open_contracts=book.open_contract_count,
                entered=entered,
                realized_pnl=realized_pnl,
                cumulative_pnl=cumulative_pnl,
                greeks=_book_greeks(closed),
                attribution=attribution,
                stress_loss=_stress_loss(closed, config.stress_grid),
            )
        )
        prior_priced = closed

    return BacktestResult(
        strategy_id=strategy.contract.strategy_id,
        days=tuple(day_results),
        summary=summarise(day_results),
    )


def _run_day(
    strategy: Strategy,
    data: BacktestData,
    book: BacktestBook,
    as_of: date,
    config: BacktestConfig,
) -> bool:
    """Run the strategy for one day and apply its entry to the book; return whether it entered.

    Drives the strategy through the *same* :func:`run_strategy` convention all four contexts use,
    so the backtest invokes the strategy identically to paper/live (the production-shadow
    property). For S2 — a capacity-gated rolling line — the daily add is gated by
    :meth:`PutLineStrategy.decide_sell` (signal ∧ capacity) using the line size from the data
    seam, which the bare protocol ``decide_entry`` does not see; a strategy without that method
    falls back to the harness's protocol entry. A fired entry's constructed legs are concretized
    to fixed contracts and opened; a leg the seam cannot concretize on the day is skipped (a
    labelled absence), never booked as a phantom.
    """
    signals = data.signals(as_of)
    market = MarketState(as_of=as_of, position_lines=book.price(data, as_of).lines)

    # The §6 harness call — identical to paper/live — produces the protocol decisions (entry,
    # exit/kill, rebalance) and, when the *protocol* entry fires, the stamped basket. The engine
    # makes it so the backtest invokes the strategy exactly as paper/live do; for a capacity-gated
    # line the operational add is decide_sell (below), which the protocol entry cannot express.
    run_strategy(
        strategy,
        context=StrategyContext.BACKTEST,
        as_of=as_of,
        signals=signals,
        market=market,
        basket_id=f"{config.basket_id_prefix}-{as_of.isoformat()}",
    )

    if not _daily_entry_fires(strategy, book.open_contract_count, signals, as_of):
        return False

    basket = strategy.construct(as_of, basket_id=f"{config.basket_id_prefix}-{as_of.isoformat()}")
    opened = _concretize_basket(data, basket, as_of)
    book.add(opened)
    return bool(opened)


def _daily_entry_fires(
    strategy: Strategy,
    open_contracts: float,
    signals: SignalSnapshot,
    as_of: date,
) -> bool:
    """Whether the strategy's daily decision opens a position today (capacity-aware for S2).

    S2's operational decision is :meth:`PutLineStrategy.decide_sell` (signal ∧ capacity), not the
    bare protocol ``decide_entry`` — the capacity gate needs the open-contract count, which the
    protocol method does not take. So for a :class:`PutLineStrategy` the engine calls
    ``decide_sell`` with the line size **from the book itself** (the booked line *is* the backtest
    book, so the capacity gate reads the real count, never a separate source that could disagree);
    any other strategy uses its protocol ``decide_entry``. Either way the result is "does the
    line/structure add today".
    """
    if isinstance(strategy, PutLineStrategy):
        decision = strategy.decide_sell(as_of, signals, open_contracts=open_contracts)
    else:
        decision = strategy.decide_entry(as_of, signals)
    return decision.action is EntryAction.ENTER


def _concretize_basket(
    data: BacktestData, basket: Basket, as_of: date
) -> list[HeldContract]:
    """Concretize a stamped basket's legs to fixed held contracts through the data seam.

    Each grid-coordinate leg is pinned to a :class:`HeldContract` (fixed strike/expiry) on its
    entry day; a leg the seam cannot resolve on the day is dropped (a labelled absence), never
    booked as a phantom. Returns the contracts to open.
    """
    opened: list[HeldContract] = []
    for leg in basket.legs:
        held = data.concretize_leg(leg, as_of)
        if held is not None:
            opened.append(held)
    return opened
