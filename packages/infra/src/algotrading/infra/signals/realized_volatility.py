from __future__ import annotations

from collections.abc import Sequence

import numpy as np

TRADING_DAYS_PER_YEAR = 252.0


class RealizedVolatilityError(ValueError):

    def __init__(self, reason: str, closes: tuple[float, ...]) -> None:
        self.reason = reason
        self.closes = closes
        super().__init__(f"realized vol undefined: {reason} (closes={closes})")


def realized_volatility(
    closes: Sequence[float],
    *,
    periods_per_year: float = TRADING_DAYS_PER_YEAR,
) -> float:
    series = tuple(float(c) for c in closes)
    if len(series) < 2:
        raise RealizedVolatilityError("need at least two closes for one return", series)
    prices = np.asarray(series, dtype=np.float64)
    if bool(np.any(prices <= 0.0)):
        raise RealizedVolatilityError("close prices must be positive for a log return", series)
    log_returns = np.diff(np.log(prices))
    daily_vol = float(np.std(log_returns, ddof=1)) if log_returns.size > 1 else 0.0
    return daily_vol * float(np.sqrt(periods_per_year))


def realized_minus_implied(realized_vol: float, implied_vol: float) -> float:
    return realized_vol - implied_vol
