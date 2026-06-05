"""The put-call-parity math behind the forward engine: parity and joint regression.

These are the small, pure numerical kernels the estimator composes. They are kept
separate from the orchestration in :mod:`forwards.estimate` so each can be tested
against an independent oracle on its own. The robust order statistics the estimator
also leans on (Theil-Sen, MAD, outlier flagging) live in
:mod:`algotrading.infra.utils.robust`, the one home for those primitives.

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
