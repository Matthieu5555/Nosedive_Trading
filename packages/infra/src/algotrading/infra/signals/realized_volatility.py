"""Realized volatility from a close-price history, and its spread against implied.

The realized-vs-implied vol spread (TARGET §3, the S2/S3 entry signal) needs the *realized*
leg: the annualized standard deviation of daily log returns over a trailing window of closes.
This module computes that leg purely (close prices in, one annualized vol out) and names the
spread convention so its sign is fixed in one tested place:

    rv_minus_iv = sigma_realized - sigma_implied   (positive => realized rich vs implied).

The annualization is ``sqrt(periods_per_year)``; ``periods_per_year`` defaults to 252 (the
trading-day convention), passed in so the convention is the caller's, not a buried literal.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

# Trading-day annualization convention; the count of return periods in a year. A genuine
# convention constant (not a tunable business parameter), so it lives in code as the default.
TRADING_DAYS_PER_YEAR = 252.0


class RealizedVolatilityError(ValueError):
    """A realized-vol window was too short or carried a non-positive price, carrying the input.

    Fewer than two closes yields no return (so no dispersion to measure), and a non-positive
    close has no log return. Raised rather than returning 0.0, which would read as "the name
    did not move" when the window simply cannot answer.
    """

    def __init__(self, reason: str, closes: tuple[float, ...]) -> None:
        self.reason = reason
        self.closes = closes
        super().__init__(f"realized vol undefined: {reason} (closes={closes})")


def realized_volatility(
    closes: Sequence[float],
    *,
    periods_per_year: float = TRADING_DAYS_PER_YEAR,
) -> float:
    """Annualized realized volatility from a trailing series of close prices.

    ``closes`` is the window in time order (oldest first); at least two are needed for one
    return. Returns the sample standard deviation (``ddof=1``) of the daily log returns scaled
    by ``sqrt(periods_per_year)``. Raises :class:`RealizedVolatilityError` for a window shorter
    than two or any non-positive price.
    """
    series = tuple(float(c) for c in closes)
    if len(series) < 2:
        raise RealizedVolatilityError("need at least two closes for one return", series)
    prices = np.asarray(series, dtype=np.float64)
    if bool(np.any(prices <= 0.0)):
        raise RealizedVolatilityError("close prices must be positive for a log return", series)
    log_returns = np.diff(np.log(prices))
    # ddof=1: a sample standard deviation (the window is a sample of the return process), the
    # standard realized-vol estimator. np.std(ddof=1) needs >= 2 returns; with exactly one
    # return it returns 0.0 — a single move has no sample dispersion, which is the honest value.
    daily_vol = float(np.std(log_returns, ddof=1)) if log_returns.size > 1 else 0.0
    return daily_vol * float(np.sqrt(periods_per_year))


def realized_minus_implied(realized_vol: float, implied_vol: float) -> float:
    """The RV−IV spread: realized minus implied, positive when realized is the richer leg."""
    return realized_vol - implied_vol
