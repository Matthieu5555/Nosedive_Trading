"""Unit tests for the backtest summary statistics — every expected value derived by hand.

The summary metrics (drawdown, Sharpe, turnover, total) are pure functions of the day path; the
oracle for each is a hand calculation stated in the test, never the code under test. These are
the lowest level that catches the statistic-arithmetic bug class (``conventions.md`` /
``tasks/TESTING.md``).
"""

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
        # Monotone-up: no trough below a peak -> zero drawdown.
        ([0.0, 1.0, 2.0, 3.0], 0.0),
        # Hand-derived: peaks [0,5,5,8,8], gaps [0,0,3,0,5] -> max 5 (the 8->3 fall).
        ([0.0, 5.0, 2.0, 8.0, 3.0], 5.0),
        # All-negative descent: peak stays at the first (0), trough -10 -> drawdown 10.
        ([0.0, -3.0, -10.0, -4.0], 10.0),
        # Empty / single point: no peak-trough pair -> 0.
        ([], 0.0),
        ([7.0], 0.0),
    ],
)
def test_maximum_drawdown_matches_hand_calculation(curve: list[float], expected: float) -> None:
    assert maximum_drawdown(curve) == pytest.approx(expected)


def test_annualised_sharpe_matches_hand_calculation() -> None:
    # Series [1, 2, 3]: mean = 2, sample variance = ((1)+(0)+(1))/2 = 1, stdev = 1.
    # Sharpe = mean/stdev * sqrt(252) = 2 * sqrt(252). Oracle is this hand formula.
    pnls = [1.0, 2.0, 3.0]
    expected = 2.0 * math.sqrt(TRADING_DAYS_PER_YEAR)
    assert annualised_sharpe(pnls) == pytest.approx(expected)


@pytest.mark.parametrize(
    "pnls",
    [
        [],            # no observations -> undefined -> 0.0
        [5.0],         # one observation -> undefined (no variance) -> 0.0
        [3.0, 3.0],    # constant -> zero variance -> 0.0 (not inf/NaN)
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
    # total = last cumulative point.
    assert summary.total_pnl == pytest.approx(14.0)
    # cumulative curve [0,10,6,14]: peaks [0,10,10,14], gaps [0,0,4,0] -> max drawdown 4.
    assert summary.max_drawdown == pytest.approx(4.0)
    # realized series (first day's None excluded) = [10, -4, 8]; sharpe via the helper.
    assert summary.sharpe == pytest.approx(annualised_sharpe([10.0, -4.0, 8.0]))
    # turnover = entered-day count (days 0,1,3).
    assert summary.turnover == 3
    # worst stress = most negative of [-100,-250,-180,-90] = -250.
    assert summary.worst_stress_loss == pytest.approx(-250.0)


def test_summarise_empty_run_is_all_zero() -> None:
    assert summarise([]) == BacktestSummary(
        total_pnl=0.0, max_drawdown=0.0, sharpe=0.0, turnover=0, worst_stress_loss=0.0,
    )
