from __future__ import annotations

import math
import statistics

import pytest
from algotrading.infra.signals import (
    RealizedVolatilityError,
    realized_minus_implied,
    realized_volatility,
)
from algotrading.infra.signals.realized_volatility import TRADING_DAYS_PER_YEAR


def _reference_realized_vol(closes: list[float], periods_per_year: float) -> float:
    log_returns = [math.log(b / a) for a, b in zip(closes, closes[1:], strict=False)]
    daily = statistics.stdev(log_returns) if len(log_returns) > 1 else 0.0
    return daily * math.sqrt(periods_per_year)


def test_matches_independent_reference() -> None:
    closes = [100.0, 110.0, 99.0, 103.0, 101.0]
    expected = _reference_realized_vol(closes, TRADING_DAYS_PER_YEAR)
    assert realized_volatility(closes) == pytest.approx(expected)


def test_annualization_scales_with_sqrt_periods() -> None:
    closes = [100.0, 110.0, 99.0, 103.0]
    base = realized_volatility(closes, periods_per_year=1.0)
    annual = realized_volatility(closes, periods_per_year=252.0)
    assert annual == pytest.approx(base * math.sqrt(252.0))


def test_constant_growth_has_zero_dispersion() -> None:
    assert realized_volatility([100.0, 110.0, 121.0]) == pytest.approx(0.0)


def test_too_few_closes_is_refused() -> None:
    with pytest.raises(RealizedVolatilityError):
        realized_volatility([100.0])


def test_non_positive_price_is_refused() -> None:
    with pytest.raises(RealizedVolatilityError):
        realized_volatility([100.0, 0.0, 101.0])


def test_spread_is_realized_minus_implied() -> None:
    assert realized_minus_implied(0.30, 0.20) == pytest.approx(0.10)
    assert realized_minus_implied(0.18, 0.25) == pytest.approx(-0.07)
