from __future__ import annotations

import math
from datetime import date, timedelta

import pytest
from algotrading.strategy.backtest.results import (
    TRADING_DAYS_PER_YEAR,
    BacktestSummary,
    DayGreeks,
    DayResult,
    annualised_sharpe,
    maximum_drawdown,
    summarise,
)


@pytest.mark.parametrize(
    ("curve", "expected"),
    [
        ([0.0, 1.0, 2.0, 3.0], 0.0),
        ([0.0, 5.0, 2.0, 8.0, 3.0], 5.0),
        ([0.0, -3.0, -10.0, -4.0], 10.0),
        ([], 0.0),
        ([7.0], 0.0),
    ],
)
def test_maximum_drawdown_matches_hand_calculation(curve: list[float], expected: float) -> None:
    assert maximum_drawdown(curve) == pytest.approx(expected)


def test_annualised_sharpe_matches_hand_calculation() -> None:
    pnls = [1.0, 2.0, 3.0]
    expected = 2.0 * math.sqrt(TRADING_DAYS_PER_YEAR)
    assert annualised_sharpe(pnls) == pytest.approx(expected)


@pytest.mark.parametrize(
    "pnls",
    [
        [],
        [5.0],
        [3.0, 3.0],
    ],
)
def test_annualised_sharpe_is_zero_when_undefined(pnls: list[float]) -> None:
    assert annualised_sharpe(pnls) == 0.0


def _day(d: date, *, realized: float | None, cumulative: float, entered: bool, stress: float) -> DayResult:
    return DayResult(
        as_of=d,
        open_contracts=0.0,
        entered=entered,
        realized_pnl=realized,
        cumulative_pnl=cumulative,
        greeks=DayGreeks(0.0, 0.0, 0.0, 0.0),
        attribution=None,
        stress_loss=stress,
    )


def test_summarise_rolls_up_the_day_path() -> None:
    base = date(2026, 1, 5)
    days = [
        _day(base, realized=None, cumulative=0.0, entered=True, stress=-100.0),
        _day(base + timedelta(1), realized=10.0, cumulative=10.0, entered=True, stress=-250.0),
        _day(base + timedelta(2), realized=-4.0, cumulative=6.0, entered=False, stress=-180.0),
        _day(base + timedelta(3), realized=8.0, cumulative=14.0, entered=True, stress=-90.0),
    ]
    summary = summarise(days)
    assert summary.total_pnl == pytest.approx(14.0)
    assert summary.max_drawdown == pytest.approx(4.0)
    assert summary.sharpe == pytest.approx(annualised_sharpe([10.0, -4.0, 8.0]))
    assert summary.turnover == 3
    assert summary.worst_stress_loss == pytest.approx(-250.0)


def test_summarise_empty_run_is_all_zero() -> None:
    assert summarise([]) == BacktestSummary(
        total_pnl=0.0, max_drawdown=0.0, sharpe=0.0, turnover=0, worst_stress_loss=0.0,
    )
