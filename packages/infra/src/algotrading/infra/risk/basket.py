from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike


class NonPSDBasketError(ValueError):

    def __init__(self, variance: float) -> None:
        self.variance = variance
        super().__init__(
            f"basket variance is negative ({variance!r}): the correlation input is not "
            "positive-semidefinite (a full matrix that is not PSD, or avg_correlation "
            "below -1/(n-1))"
        )


@dataclass(frozen=True)
class BasketVarianceResult:

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
    w = np.asarray(weights, dtype=np.float64)
    s = np.asarray(vols, dtype=np.float64)
    if w.shape != s.shape or w.ndim != 1:
        raise ValueError(
            f"weights and vols must be 1-D of equal length, got {w.shape} and {s.shape}"
        )
    if (correlations is None) == (avg_correlation is None):
        raise ValueError("provide exactly one of correlations or avg_correlation")

    ws = w * s
    fully_correlated_vol = float(np.abs(ws).sum())
    if correlations is not None:
        corr = np.asarray(correlations, dtype=np.float64)
        n = w.shape[0]
        if corr.shape != (n, n):
            raise ValueError(f"correlations must be {n}x{n}, got {corr.shape}")
        variance = float(ws @ corr @ ws)
    else:
        rho = float(avg_correlation)  # type: ignore[arg-type]
        own = float(np.dot(ws, ws))
        cross = float(ws.sum() ** 2 - own)
        variance = own + rho * cross

    psd_floor = -1e-12 * fully_correlated_vol * fully_correlated_vol
    if variance < psd_floor:
        raise NonPSDBasketError(variance)
    variance = max(variance, 0.0)

    vol = math.sqrt(variance)
    diversification_ratio = vol / fully_correlated_vol if fully_correlated_vol > 0 else 0.0
    return BasketVarianceResult(
        variance=variance, vol=vol, diversification_ratio=diversification_ratio
    )
