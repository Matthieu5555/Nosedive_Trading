"""The research-backtester output record — serious output, not a Sharpe number (TARGET §5.7).

TARGET §5.7 is explicit about what a backtest must produce: *"performance, drawdowns,
turnover, exposure, Greeks, stress losses, **and attribution through time** ('returns came
from short vega and positive carry', not 'Sharpe 1.4')."* This module is the typed home of
that output — one immutable :class:`DayResult` per replay day and a :class:`BacktestResult`
that carries the whole day-by-day path plus the summary metrics derived from it.

Nothing here computes risk or attribution: the per-day Greeks come from the landed
:func:`~algotrading.infra.risk.greeks.position_risk` lines, the per-day attribution from the
landed :func:`~algotrading.infra.risk.attribution.attribute_realized_book`
(``RealizedBookAttribution``), and the stress loss from the landed
:func:`~algotrading.infra.risk.scenarios.worst_case` over the same lines — the engine assembles
those landed results into one record; this module only *holds* them and computes the pure,
hand-checkable summary statistics (cumulative return, max drawdown, annualised Sharpe, turnover)
off the realized P&L path. Every summary number traces to one function here, so a report
regenerates from the day path alone.

The attribution-through-time view is the whole point: ``attribution_by_day`` is the per-day
``RealizedBookAttribution`` and :meth:`BacktestResult.cumulative_attribution` sums its named
terms across the stretch, so a reader sees *which Greek paid* over time, not just the total.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from algotrading.infra.risk.attribution import RealizedBookAttribution
from algotrading.infra.risk.scenarios import TaylorTerms

# A year of trading days — the annualisation factor for the Sharpe statistic. An internal
# statistical constant (not an economic/business parameter), so it lives in code per the ADR-0028
# carve-out for "genuine internal invariants". 252 is the standard equity-calendar count.
TRADING_DAYS_PER_YEAR = 252


@dataclass(frozen=True, slots=True)
class DayGreeks:
    """The book-level dollar Greeks held at the close of one replay day (the exposure line).

    Summed across the held lines from the landed per-line :class:`PositionRisk` Greeks (the
    same numbers attribution reads), so the exposure path and the attribution path never
    disagree on the book's Greeks. An empty book is all-zero (a flat day carries no risk).
    """

    delta: float
    gamma: float
    vega: float
    theta: float


@dataclass(frozen=True, slots=True)
class DayResult:
    """One replay day's full result line — the row of the through-time table (TARGET §5.7).

    * ``as_of`` — the day this row is for (the look-ahead anchor; everything on the row is a
      function of state at or before it).
    * ``open_contracts`` — the size of the rolling line carried into the day (S2's capacity input).
    * ``entered`` — whether the strategy opened a new position this day (drives turnover).
    * ``realized_pnl`` — the day-over-day mark-to-market P&L of the book held *into* the day,
      the full-reprice oracle from :class:`RealizedBookAttribution` (``None`` on the first day
      and any flat day, where there is no prior book to mark).
    * ``cumulative_pnl`` — the running sum of ``realized_pnl`` (the equity curve point).
    * ``greeks`` — the book's close-of-day dollar Greeks (the exposure line).
    * ``attribution`` — the day's per-Greek P&L decomposition + residual
      (``RealizedBookAttribution``), or ``None`` on a day with no prior book to attribute.
    * ``stress_loss`` — the worst-case full-reprice loss over the configured stress grid on the
      day's book (a negative number; ``0.0`` for a flat book), the §5.7 "stress losses" column.
    """

    as_of: date
    open_contracts: float
    entered: bool
    realized_pnl: float | None
    cumulative_pnl: float
    greeks: DayGreeks
    attribution: RealizedBookAttribution | None
    stress_loss: float


@dataclass(frozen=True, slots=True)
class BacktestSummary:
    """The summary statistics over the whole replay stretch — each from one pure function here.

    * ``total_pnl`` — the final cumulative realized P&L (the equity curve's last point).
    * ``max_drawdown`` — the largest peak-to-trough drop in the cumulative-P&L curve (a
      non-negative number; ``0.0`` for a monotone-up or flat curve).
    * ``sharpe`` — the annualised ratio of mean to standard deviation of the daily realized P&L
      (excess over a zero risk-free rate; ``0.0`` when there are fewer than two P&L observations
      or the P&L is constant, where a ratio is undefined rather than infinite).
    * ``turnover`` — the count of days the strategy entered a new position (the line's add count;
      for the rolling S2 line this is "how many puts were sold over the stretch").
    * ``worst_stress_loss`` — the most negative ``stress_loss`` seen on any day (the deepest the
      stress grid ever marked the live book down).
    """

    total_pnl: float
    max_drawdown: float
    sharpe: float
    turnover: int
    worst_stress_loss: float


@dataclass(frozen=True, slots=True)
class BacktestResult:
    """The full research-backtest output: the day-by-day path plus the derived summary.

    ``days`` is the ordered per-day record (the through-time table); ``summary`` is the pure
    rollup over it. :meth:`cumulative_attribution` is the headline view TARGET §5.7 asks for —
    the named per-Greek terms summed across the stretch, so "returns came from short vega and
    positive carry" is a number a reader can read straight off, not a story.
    """

    strategy_id: str
    days: tuple[DayResult, ...]
    summary: BacktestSummary

    @property
    def attribution_by_day(self) -> tuple[RealizedBookAttribution | None, ...]:
        """The per-day realized attribution, in day order (``None`` on a no-prior-book day)."""
        return tuple(day.attribution for day in self.days)

    def cumulative_attribution(self) -> TaylorTerms:
        """The named per-Greek P&L summed across every attributed day (the §5.7 headline view).

        Each term (delta/gamma/vega/theta/rho/vanna/volga) is the ``math.fsum`` of that term
        across the days that carried an attribution — so the reader sees *which Greek paid* over
        the whole stretch. Reorder-invariant (fsum) and a pure function of ``days``; a stretch
        with no attributed day is all-zero terms (nothing was held to attribute).
        """
        attributed = [day.attribution for day in self.days if day.attribution is not None]
        return TaylorTerms(
            delta_pnl=math.fsum(a.terms.delta_pnl for a in attributed),
            gamma_pnl=math.fsum(a.terms.gamma_pnl for a in attributed),
            vega_pnl=math.fsum(a.terms.vega_pnl for a in attributed),
            theta_pnl=math.fsum(a.terms.theta_pnl for a in attributed),
            rho_pnl=math.fsum(a.terms.rho_pnl for a in attributed),
            vanna_pnl=math.fsum(a.terms.vanna_pnl for a in attributed),
            volga_pnl=math.fsum(a.terms.volga_pnl for a in attributed),
        )


def maximum_drawdown(cumulative_curve: Sequence[float]) -> float:
    """The largest peak-to-trough drop in a cumulative-P&L curve (non-negative).

    Walks the curve once, tracking the running peak, and returns the deepest ``peak - point``
    gap. A monotone-up curve (or an empty/one-point curve) has drawdown ``0.0`` — there is no
    trough below a peak. Hand-checkable: for ``[0, 5, 2, 8, 3]`` the peaks are ``[0,5,5,8,8]``
    and the gaps ``[0,0,3,0,5]``, so the max drawdown is ``5`` (the 8→3 fall).
    """
    peak = -math.inf
    worst = 0.0
    for point in cumulative_curve:
        peak = max(peak, point)
        worst = max(worst, peak - point)
    return worst


def annualised_sharpe(daily_pnls: Sequence[float]) -> float:
    """The annualised Sharpe of a daily realized-P&L series (zero risk-free, ``0.0`` if undefined).

    Sharpe is ``mean / stdev * sqrt(252)`` over the daily P&L. Returns ``0.0`` rather than a
    NaN/inf when the ratio is undefined — fewer than two observations, or a zero standard
    deviation (a constant series has no risk-adjusted signal). Uses the *sample* standard
    deviation (``ddof = 1``) so a two-point series is not divided by zero degrees of freedom.
    """
    pnls = list(daily_pnls)
    if len(pnls) < 2:
        return 0.0
    mean = math.fsum(pnls) / len(pnls)
    variance = math.fsum((p - mean) ** 2 for p in pnls) / (len(pnls) - 1)
    if variance <= 0.0:
        return 0.0
    return mean / math.sqrt(variance) * math.sqrt(TRADING_DAYS_PER_YEAR)


def summarise(days: Sequence[DayResult]) -> BacktestSummary:
    """Roll the per-day path up into the :class:`BacktestSummary` — each metric one pure call.

    Pure function of the day path: ``total_pnl`` is the last cumulative point (``0.0`` for an
    empty run), ``max_drawdown`` and ``sharpe`` come from the helpers above over the realized
    series, ``turnover`` counts the entered days, ``worst_stress_loss`` is the min stress loss.
    """
    if not days:
        return BacktestSummary(
            total_pnl=0.0, max_drawdown=0.0, sharpe=0.0, turnover=0, worst_stress_loss=0.0,
        )
    realized = [day.realized_pnl for day in days if day.realized_pnl is not None]
    cumulative = [day.cumulative_pnl for day in days]
    return BacktestSummary(
        total_pnl=cumulative[-1],
        max_drawdown=maximum_drawdown(cumulative),
        sharpe=annualised_sharpe(realized),
        turnover=sum(1 for day in days if day.entered),
        worst_stress_loss=min(day.stress_loss for day in days),
    )
