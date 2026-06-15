"""The research backtester (TARGET §5.7 / §7.8) — "does this idea have edge?".

The first of the two §5.7 machines: replay a strategy over banked history day by day through the
*same* §6 four-context harness paper/live use, producing the serious output — performance,
drawdowns, turnover, exposure, Greeks, stress losses, and attribution through time. It reinvents
no substrate: the strategy runs through the landed harness, the book prices into landed
:class:`PositionRisk` lines, the day-over-day P&L is decomposed by the landed realized
attribution engine, and the stress loss is the landed worst-case scenario. The **production
shadow** machine ("would my live system have produced this P&L?") is the deliberate second build
and is not here — see the strategy README's backtester section for that scope line.

Public surface:

* :func:`run_backtest` / :class:`BacktestConfig` — the engine and its injected config.
* :class:`BacktestResult` / :class:`DayResult` / :class:`BacktestSummary` — the day-by-day output
  and the rolled-up metrics; :meth:`BacktestResult.cumulative_attribution` is the §5.7 headline
  through-time view (which Greek paid, summed across the stretch).
* :class:`BacktestData` / :class:`InMemoryBacktestData` / :class:`HeldContract` — the as-of
  market-state seam (the look-ahead boundary) and its hand-checkable reference implementor.
* :class:`BacktestBook` — the running held-contract ledger that prices into the landed engine.
"""

from __future__ import annotations

from .book import BacktestBook, PricedBook
from .data import (
    BacktestData,
    ContractMarks,
    HeldContract,
    InMemoryBacktestData,
)
from .engine import BacktestConfig, run_backtest
from .results import (
    BacktestResult,
    BacktestSummary,
    DayGreeks,
    DayResult,
    annualised_sharpe,
    maximum_drawdown,
    summarise,
)

__all__ = [
    "BacktestBook",
    "BacktestConfig",
    "BacktestData",
    "BacktestResult",
    "BacktestSummary",
    "ContractMarks",
    "DayGreeks",
    "DayResult",
    "HeldContract",
    "InMemoryBacktestData",
    "PricedBook",
    "annualised_sharpe",
    "maximum_drawdown",
    "run_backtest",
    "summarise",
]
