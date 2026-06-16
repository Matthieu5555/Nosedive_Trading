from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from algotrading.infra.risk.attribution import RealizedBookAttribution
from algotrading.infra.risk.scenarios import TaylorTerms

TRADING_DAYS_PER_YEAR = 252


@dataclass(frozen=True, slots=True)
class DayGreeks:

    delta: float
    gamma: float
    vega: float
    theta: float


@dataclass(frozen=True, slots=True)
class DayResult:

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

    total_pnl: float
    max_drawdown: float
    sharpe: float
    turnover: int
    worst_stress_loss: float


@dataclass(frozen=True, slots=True)
class BacktestResult:

    strategy_id: str
    days: tuple[DayResult, ...]
    summary: BacktestSummary

    @property
    def attribution_by_day(self) -> tuple[RealizedBookAttribution | None, ...]:
        return tuple(day.attribution for day in self.days)

    def cumulative_attribution(self) -> TaylorTerms:
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
    peak = -math.inf
    worst = 0.0
    for point in cumulative_curve:
        peak = max(peak, point)
        worst = max(worst, peak - point)
    return worst


def annualised_sharpe(daily_pnls: Sequence[float]) -> float:
    pnls = list(daily_pnls)
    if len(pnls) < 2:
        return 0.0
    mean = math.fsum(pnls) / len(pnls)
    variance = math.fsum((p - mean) ** 2 for p in pnls) / (len(pnls) - 1)
    if variance <= 0.0:
        return 0.0
    return mean / math.sqrt(variance) * math.sqrt(TRADING_DAYS_PER_YEAR)


def summarise(days: Sequence[DayResult]) -> BacktestSummary:
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
