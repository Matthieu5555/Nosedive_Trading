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

    # Diversification vs the fully-correlated case (rho = 1 everywhere -> vol is the weighted sum).
    fully_correlated_vol = float(np.abs(ws).sum())
    vol = math.sqrt(variance) if variance > 0 else 0.0
    diversification_ratio = vol / fully_correlated_vol if fully_correlated_vol > 0 else 0.0
    return BasketVarianceResult(
        variance=variance, vol=vol, diversification_ratio=diversification_ratio
    )
