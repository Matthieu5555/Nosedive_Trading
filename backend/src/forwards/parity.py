"""The put-call-parity math behind the forward engine: parity, regression, MAD.

These are the small, pure numerical kernels the estimator composes. They are kept
separate from the orchestration in :mod:`forwards.estimate` so each can be tested
against an independent oracle on its own.

The one relation everything rests on is put-call parity for a European pair on the
same strike and expiry (roadmap Eq 2):

    C - P = DF * (F - K)

Read as a function of strike across a chain, the left side ``y = C - P`` is *linear*
in ``K``:

    y(K) = DF * F - DF * K = intercept + slope * K,   slope = -DF,  intercept = DF * F

so a single weighted least-squares line through the ``(K, C - P)`` points recovers
both unknowns at once: ``DF = -slope`` and ``F = intercept / DF``. That joint
recovery is why the engine never needs a discount factor handed to it — it reads
one straight off the chain. A single pair gives one equation in two unknowns, so it
cannot identify both; that degenerate case is the estimator's business, not this
module's.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import median


def parity_forward_from_pair(
    call_mid: float, put_mid: float, strike: float, discount_factor: float
) -> float:
    """Forward implied by one call/put pair at a known discount factor (Eq 2).

    Rearranges ``C - P = DF * (F - K)`` to ``F = K + (C - P) / DF``. Used for the
    single-pair fallback, where a discount factor must be supplied because one pair
    cannot identify it.
    """
    return strike + (call_mid - put_mid) / discount_factor


@dataclass(frozen=True, slots=True)
class ParityLine:
    """The fitted ``y = intercept + slope * K`` line and the forward/DF it implies.

    ``discount_factor`` is ``-slope`` and ``forward`` is ``intercept / discount_factor``.
    ``residuals`` are ``y_i - (intercept + slope * K_i)`` in the input order, so the
    estimator can score the fit and reject outliers against them.
    """

    intercept: float
    slope: float
    discount_factor: float
    forward: float
    residuals: tuple[float, ...]


class DegenerateParityFit(Exception):
    """The parity regression could not be solved or gave an unphysical forward/DF.

    Carries a plain-language reason: too few distinct strikes with positive weight,
    a singular normal equation, or a recovered ``DF``/``F`` outside its valid domain
    (``DF`` in ``(0, 1]``, ``F > 0``). The estimator turns this into a labeled
    low-confidence result rather than letting it propagate as a crash.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def regress_forward_and_discount_factor(
    strikes: tuple[float, ...],
    parity_spreads: tuple[float, ...],
    weights: tuple[float, ...],
) -> ParityLine:
    """Weighted least-squares of ``C - P`` on ``K``; recover forward and DF jointly.

    ``parity_spreads[i]`` is ``call_mid - put_mid`` at ``strikes[i]``; ``weights[i]``
    is the non-negative liquidity weight for that strike (Eq 4). Minimizes
    ``sum_i w_i (y_i - (a + b K_i))**2`` in closed form. Raises
    :class:`DegenerateParityFit` when the fit is unidentified (fewer than two
    distinct positively-weighted strikes, so the normal equation is singular) or
    when the recovered ``DF``/``F`` leaves its valid domain.
    """
    positive = [
        (k, y, w) for k, y, w in zip(strikes, parity_spreads, weights, strict=True) if w > 0.0
    ]
    if len({k for k, _, _ in positive}) < 2:
        raise DegenerateParityFit("need at least two distinct strikes with positive weight")

    sum_w = math.fsum(w for _, _, w in positive)
    sum_wx = math.fsum(w * k for k, _, w in positive)
    sum_wy = math.fsum(w * y for _, y, w in positive)
    sum_wxx = math.fsum(w * k * k for k, _, w in positive)
    sum_wxy = math.fsum(w * k * y for k, y, w in positive)

    denominator = sum_w * sum_wxx - sum_wx * sum_wx
    if denominator <= 0.0:  # pragma: no cover - unreachable after the distinct-strike guard
        # By Cauchy-Schwarz this is >= 0, and zero only when every weighted strike
        # shares one value, which the distinct-strike guard above already rejects. Kept
        # as defense in depth against float cancellation rather than a divide-by-zero.
        raise DegenerateParityFit("singular normal equation (strikes not spread out)")

    slope = (sum_w * sum_wxy - sum_wx * sum_wy) / denominator
    intercept = (sum_wxx * sum_wy - sum_wx * sum_wxy) / denominator

    discount_factor = -slope
    if not (0.0 < discount_factor <= 1.0):
        raise DegenerateParityFit(
            f"implied discount factor {discount_factor!r} is outside (0, 1]"
        )
    forward = intercept / discount_factor
    if not (math.isfinite(forward) and forward > 0.0):
        raise DegenerateParityFit(f"implied forward {forward!r} is not a positive number")

    residuals = tuple(
        y - (intercept + slope * k)
        for k, y, _ in zip(strikes, parity_spreads, weights, strict=True)
    )
    return ParityLine(
        intercept=intercept,
        slope=slope,
        discount_factor=discount_factor,
        forward=forward,
        residuals=residuals,
    )


def theil_sen_line(
    strikes: tuple[float, ...], parity_spreads: tuple[float, ...]
) -> tuple[float, float]:
    """Robust ``(slope, intercept)`` for ``C - P`` vs ``K`` via the Theil-Sen estimator.

    The slope is the median of the pairwise slopes over every distinct-strike pair;
    the intercept is the median of ``y_i - slope * K_i``. Both are medians, so the
    line ignores a minority of outliers — *including high-leverage wing strikes*,
    which ordinary least squares chases (a wing outlier has a small OLS residual yet
    badly tilts the slope, masking itself). This robust line is used only to produce
    clean residuals for MAD outlier flagging (Eq 24); the final forward and discount
    factor still come from the liquidity-weighted least-squares fit on the inliers.

    Raises :class:`DegenerateParityFit` when no distinct-strike pair exists, so a
    slope cannot be formed at all.
    """
    slopes = [
        (parity_spreads[j] - parity_spreads[i]) / (strikes[j] - strikes[i])
        for i in range(len(strikes))
        for j in range(i + 1, len(strikes))
        if strikes[j] != strikes[i]
    ]
    if not slopes:
        raise DegenerateParityFit("no distinct-strike pair to form a robust slope")
    slope = median(slopes)
    intercept = median([y - slope * k for k, y in zip(strikes, parity_spreads, strict=True)])
    return slope, intercept


def median_absolute_deviation(values: tuple[float, ...]) -> float:
    """Median absolute deviation from the median (Eq 24), unscaled.

    ``MAD = median(|x_i - median(x)|)``. Zero for a perfect (or near-perfect) fit,
    which is exactly the synthetic case, so a zero MAD must mean "no outliers", never
    "reject everything". Returns ``0.0`` for an empty input.
    """
    if not values:
        return 0.0
    center = median(values)
    return median(tuple(abs(value - center) for value in values))


# Robust z-score cut-off for outlier rejection. A residual whose distance from the
# median residual exceeds this many scaled-MAD units is rejected (Eq 24). 3.5 is the
# common Iglewicz-Hoaglin cut-off; lower rejects more aggressively. The 1.4826 factor
# rescales MAD to a standard-deviation-consistent estimate for normal noise.
_MAD_REJECTION_Z = 3.5
_MAD_TO_SIGMA = 1.4826


def outlier_flags(residuals: tuple[float, ...], *, scale_floor: float = 0.0) -> tuple[bool, ...]:
    """Per-residual rejection flags via the robust MAD z-score (Eq 24).

    A point is flagged when ``|r_i - median(r)| / scale > 3.5``, where ``scale`` is
    ``max(1.4826 * MAD, scale_floor)``. The floor matters: when most points lie on
    the line, the MAD of residuals collapses to floating-point noise (not a real
    spread), and an unfloored z-score would divide by ~1e-15 and spuriously flag
    clean points. ``scale_floor`` is the quote-noise scale below which a deviation is
    rounding, not an outlier — the caller sets it from the price level. With fewer
    than three residuals nothing is flagged (too few to estimate spread); the caller
    additionally guards the minimum surviving count.
    """
    if len(residuals) < 3:
        return tuple(False for _ in residuals)
    center = median(residuals)
    mad = median_absolute_deviation(residuals)
    scale = max(_MAD_TO_SIGMA * mad, scale_floor)
    if scale <= 0.0:
        return tuple(False for _ in residuals)
    return tuple(abs(residual - center) / scale > _MAD_REJECTION_Z for residual in residuals)
