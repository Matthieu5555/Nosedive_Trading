"""Generic basket / index variance identity (Eq. 23).

A reusable risk-and-diagnostics primitive (not strategy logic): given constituent weights and
volatilities plus either a full pairwise correlation matrix or a single average-correlation
assumption, return the implied basket variance and a diversification diagnostic.

    sigma_I^2 = sum_i w_i^2 sigma_i^2 + sum_{i != j} w_i w_j rho_ij sigma_i sigma_j
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike


class NonPSDBasketError(ValueError):
    """The correlation input implied a negative basket variance — a non-PSD input.

    A full correlation matrix that is not positive-semidefinite, or an
    ``avg_correlation`` below ``-1/(n-1)`` (the lower bound for an equicorrelation
    matrix of ``n`` assets to stay PSD), yields ``sigma_I^2 < 0``: an impossible
    variance. Flooring its vol to 0.0 would silently present a degenerate input as a
    *maximally diversified* basket, hiding a corrupt correlation. It is raised, carrying
    the computed negative variance, so the offending value is visible at the boundary.
    """

    def __init__(self, variance: float) -> None:
        self.variance = variance
        super().__init__(
            f"basket variance is negative ({variance!r}): the correlation input is not "
            "positive-semidefinite (a full matrix that is not PSD, or avg_correlation "
            "below -1/(n-1))"
        )


@dataclass(frozen=True)
class BasketVarianceResult:
    """Implied basket variance and its diagnostics.

    ``diversification_ratio`` is the basket vol divided by the fully-correlated vol (the weighted
    sum of constituent vols): 1.0 means no diversification benefit, lower means more.
    """

    variance: float
    vol: float
    diversification_ratio: float


def basket_variance(
    weights: ArrayLike,
    vols: ArrayLike,
    *,
    correlations: ArrayLike | None = None,
    avg_correlation: float | None = None,
) -> BasketVarianceResult:
    """Implied basket variance (Eq. 23) from weights, constituent vols, and a correlation input.

    Provide EXACTLY ONE of ``correlations`` (a full pairwise matrix, unit diagonal) or
    ``avg_correlation`` (a single off-diagonal correlation applied to every distinct pair).
    """
    w = np.asarray(weights, dtype=np.float64)
    s = np.asarray(vols, dtype=np.float64)
    if w.shape != s.shape or w.ndim != 1:
        raise ValueError(
            f"weights and vols must be 1-D of equal length, got {w.shape} and {s.shape}"
        )
    if (correlations is None) == (avg_correlation is None):
        raise ValueError("provide exactly one of correlations or avg_correlation")

    ws = w * s
    # The fully-correlated variance (rho == 1 everywhere) is the magnitude scale: it is an
    # upper bound on |variance|, so it sets the size of a "rounding noise" zero.
    fully_correlated_vol = float(np.abs(ws).sum())
    if correlations is not None:
        corr = np.asarray(correlations, dtype=np.float64)
        n = w.shape[0]
        if corr.shape != (n, n):
            raise ValueError(f"correlations must be {n}x{n}, got {corr.shape}")
        variance = float(ws @ corr @ ws)
    else:
        rho = float(avg_correlation)  # type: ignore[arg-type]
        own = float(np.dot(ws, ws))  # sum_i w_i^2 sigma_i^2
        cross = float(ws.sum() ** 2 - own)  # sum_{i != j} w_i w_j sigma_i sigma_j
        variance = own + rho * cross

    # A genuinely negative variance is impossible (the correlation input is non-PSD); a
    # negative within rounding noise of the scale is the exact-zero PSD boundary computed
    # in floating point. Distinguish them by a scale-relative tolerance: floor the boundary
    # to 0.0, but RAISE on a real non-PSD input (carrying the offending value) rather than
    # silently presenting a corrupt input as a maximally-diversified basket.
    psd_floor = -1e-12 * fully_correlated_vol * fully_correlated_vol
    if variance < psd_floor:
        raise NonPSDBasketError(variance)
    variance = max(variance, 0.0)

    # Diversification vs the fully-correlated case (rho = 1 everywhere -> vol is the weighted sum).
    vol = math.sqrt(variance)
    diversification_ratio = vol / fully_correlated_vol if fully_correlated_vol > 0 else 0.0
    return BasketVarianceResult(
        variance=variance, vol=vol, diversification_ratio=diversification_ratio
    )
