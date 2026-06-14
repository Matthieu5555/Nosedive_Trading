"""Robust order-statistics primitives: median absolute deviation, MAD z-score, robust
regression, outlier flagging, and weighted median.

Median-based statistics resist contamination by a few bad quotes far better than the mean
and standard deviation, which is why the forward, surface, and anomaly engines screen
candidates with them. The canonical robust score is the MAD z-score:

    z_i = (x_i - median(x)) / (1.4826 * MAD(x))

where ``MAD(x) = median(|x_i - median(x)|)`` and the 1.4826 factor rescales MAD into a
consistent estimator of the standard deviation under normality.

These work in ``float``: the analytics pipeline they feed is float throughout, and its
determinism is anchored by the golden + cross-process-hash machinery, not by decimal
exactness. Degenerate spread (zero MAD) is handled explicitly at every entry point so a
caller never divides by zero or silently rejects a clean set.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from statistics import median

# Rescales MAD into a consistent estimator of the std-dev under normality (1 / Phi^-1(0.75)).
MAD_SCALE = 1.4826

# Robust z-score cut-off for residual outlier flagging (Eq 24). A residual whose distance
# from the median residual exceeds this many scaled-MAD units is flagged. 3.5 is the common
# Iglewicz-Hoaglin cut-off; lower flags more aggressively.
_MAD_REJECTION_Z = 3.5


def median_absolute_deviation(values: Sequence[float]) -> float:
    """Median absolute deviation from the median: ``median(|x_i - median(x)|)`` (Eq 24).

    Unscaled. Zero for a perfect (or near-perfect) fit — so a zero MAD must mean "no
    spread to measure", never "reject everything". Returns ``0.0`` for an empty input.
    """
    if not values:
        return 0.0
    center = median(values)
    return median([abs(value - center) for value in values])


def robust_zscores(values: Sequence[float]) -> tuple[float | None, ...]:
    """Robust z-score per value: ``(x_i - median) / (1.4826 * MAD)``.

    Returns ``None`` for every entry when MAD is zero — the scale is then undefined (no
    dispersion to measure against), so callers never divide by zero.
    """
    center = median(values)
    scale = MAD_SCALE * median_absolute_deviation(values)
    if scale == 0.0:
        return tuple(None for _ in values)
    return tuple((value - center) / scale for value in values)


def robust_zscore_vs_baseline(value: float, baseline: Sequence[float]) -> float:
    """Robust (MAD) z-score of a single ``value`` against a ``baseline`` distribution.

    Unlike :func:`robust_zscores` (which scores a set against itself), this scores an
    external value against the baseline's own median and MAD — the form anomaly detection
    needs. A degenerate baseline (zero MAD) scores ``0.0`` when ``value`` equals the median,
    else ``+/-inf``: a deviation off a flat baseline is an unbounded anomaly, never silently
    zero.
    """
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
    """Per-residual rejection flags via the robust MAD z-score (Eq 24).

    A point is flagged (``True`` = reject) when ``|r_i - median(r)| / scale > rejection_z``,
    where ``scale`` is ``max(1.4826 * MAD, scale_floor)``. The floor matters: when most points
    lie on the line, the MAD of residuals collapses to floating-point noise (not a real
    spread), and an unfloored z-score would divide by ~1e-15 and spuriously flag clean points.
    ``scale_floor`` is the quote-noise scale below which a deviation is rounding, not an
    outlier — the caller sets it from the price level. ``rejection_z`` is the cut-off in
    scaled-MAD units; it defaults to the library's Iglewicz-Hoaglin 3.5 so existing callers
    are unchanged, and a caller (e.g. the forward engine, from typed config) may tighten or
    loosen it. With fewer than three residuals nothing is flagged (too few to estimate
    spread); the caller additionally guards the minimum surviving count.
    """
    if len(residuals) < 3:
        return tuple(False for _ in residuals)
    center = median(residuals)
    scale = max(MAD_SCALE * median_absolute_deviation(residuals), scale_floor)
    if scale <= 0.0:
        return tuple(False for _ in residuals)
    return tuple(abs(residual - center) / scale > rejection_z for residual in residuals)


def theil_sen_line(xs: Sequence[float], ys: Sequence[float]) -> tuple[float, float]:
    """Robust ``(slope, intercept)`` of ``ys`` on ``xs`` via the Theil-Sen estimator.

    The slope is the median of the pairwise slopes over every distinct-``x`` pair; the
    intercept is the median of ``y_i - slope * x_i``. Both are medians, so the line ignores a
    minority of outliers — including high-leverage points that ordinary least squares chases
    (a leverage outlier has a small OLS residual yet badly tilts the slope, masking itself).

    Raises:
        ValueError: when no distinct-``x`` pair exists, so a slope cannot be formed at all.
    """
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
    """Weighted median: smallest value whose cumulative weight reaches half the total.

    Order-independent. Raises ``ValueError`` for an empty input (no median to take).
    """
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
