from __future__ import annotations

import math
from collections.abc import Sequence
from statistics import median

MAD_SCALE = 1.4826

_MAD_REJECTION_Z = 3.5


def median_absolute_deviation(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    center = median(values)
    return median([abs(value - center) for value in values])


def robust_zscores(values: Sequence[float]) -> tuple[float | None, ...]:
    center = median(values)
    scale = MAD_SCALE * median_absolute_deviation(values)
    if scale == 0.0:
        return tuple(None for _ in values)
    return tuple((value - center) / scale for value in values)


def robust_zscore_vs_baseline(value: float, baseline: Sequence[float]) -> float:
    center = median(baseline)
    diff = value - center
    scale = MAD_SCALE * median_absolute_deviation(baseline)
    if scale == 0.0:
        if diff == 0.0:
            return 0.0
        return math.inf if diff > 0.0 else -math.inf
    return diff / scale


def outlier_flags(
    residuals: Sequence[float],
    *,
    scale_floor: float = 0.0,
    rejection_z: float = _MAD_REJECTION_Z,
) -> tuple[bool, ...]:
    if len(residuals) < 3:
        return tuple(False for _ in residuals)
    center = median(residuals)
    scale = max(MAD_SCALE * median_absolute_deviation(residuals), scale_floor)
    if scale <= 0.0:
        return tuple(False for _ in residuals)
    return tuple(abs(residual - center) / scale > rejection_z for residual in residuals)


def theil_sen_line(xs: Sequence[float], ys: Sequence[float]) -> tuple[float, float]:
    slopes = [
        (ys[j] - ys[i]) / (xs[j] - xs[i])
        for i in range(len(xs))
        for j in range(i + 1, len(xs))
        if xs[j] != xs[i]
    ]
    if not slopes:
        raise ValueError("no distinct-x pair to form a robust slope")
    slope = median(slopes)
    intercept = median([y - slope * x for x, y in zip(xs, ys, strict=True)])
    return slope, intercept


def weighted_median(values: Sequence[float], weights: Sequence[float]) -> float:
    order = sorted(zip(values, weights, strict=True), key=lambda vw: vw[0])
    if not order:
        raise ValueError("weighted_median of an empty sequence is undefined")
    half = math.fsum(weights) / 2.0
    cumulative = 0.0
    for value, weight in order:
        cumulative += weight
        if cumulative >= half:
            return value
    return order[-1][0]
