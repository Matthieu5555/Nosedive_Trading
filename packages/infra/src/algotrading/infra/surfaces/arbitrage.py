from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from .svi import SviParams

_CALENDAR_TOL = 1e-9
_BUTTERFLY_TOL = 1e-9


@dataclass(frozen=True, slots=True)
class CalendarViolation:

    k: float
    maturity_short: float
    maturity_long: float
    w_short: float
    w_long: float


def calendar_violations(
    slices: Sequence[tuple[float, Callable[[float], float]]],
    k_grid: tuple[float, ...],
) -> tuple[CalendarViolation, ...]:
    ordered = sorted(slices, key=lambda item: item[0])
    violations: list[CalendarViolation] = []
    for (t_short, w_short_fn), (t_long, w_long_fn) in zip(ordered, ordered[1:], strict=False):
        for k in k_grid:
            w_short = w_short_fn(k)
            w_long = w_long_fn(k)
            if w_long < w_short - _CALENDAR_TOL:
                violations.append(
                    CalendarViolation(k, t_short, t_long, w_short, w_long)
                )
    return tuple(violations)


def butterfly_g(params: SviParams, k: float) -> float:
    w = params.total_variance(k)
    w1 = params.first_derivative(k)
    w2 = params.second_derivative(k)
    term_1 = (1.0 - k * w1 / (2.0 * w)) ** 2
    term_2 = (w1 / 2.0) ** 2 * (1.0 / w + 0.25)
    return term_1 - term_2 + w2 / 2.0


def butterfly_violations(params: SviParams, k_grid: tuple[float, ...]) -> tuple[float, ...]:
    violations: list[float] = []
    for k in k_grid:
        if params.total_variance(k) <= 0.0 or butterfly_g(params, k) < -_BUTTERFLY_TOL:
            violations.append(k)
    return tuple(violations)
