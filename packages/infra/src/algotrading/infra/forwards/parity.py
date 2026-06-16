from __future__ import annotations

import math
from dataclasses import dataclass


def parity_forward_from_pair(
    call_mid: float, put_mid: float, strike: float, discount_factor: float
) -> float:
    return strike + (call_mid - put_mid) / discount_factor


@dataclass(frozen=True, slots=True)
class ParityLine:

    intercept: float
    slope: float
    discount_factor: float
    forward: float
    residuals: tuple[float, ...]


class DegenerateParityFit(Exception):

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def regress_forward_and_discount_factor(
    strikes: tuple[float, ...],
    parity_spreads: tuple[float, ...],
    weights: tuple[float, ...],
) -> ParityLine:
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
