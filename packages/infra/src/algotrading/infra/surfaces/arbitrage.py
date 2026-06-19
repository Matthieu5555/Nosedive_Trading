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
    support_min: float | None = None
    support_max: float | None = None


@dataclass(frozen=True, slots=True)
class CalendarSlice:

    maturity_years: float
    total_variance: Callable[[float], float]
    observed_k_min: float | None = None
    observed_k_max: float | None = None


def _support_intersection(
    short: CalendarSlice, long: CalendarSlice
) -> tuple[float | None, float | None]:
    mins = [b for b in (short.observed_k_min, long.observed_k_min) if b is not None]
    maxs = [b for b in (short.observed_k_max, long.observed_k_max) if b is not None]
    support_min = max(mins) if len(mins) == 2 else None
    support_max = min(maxs) if len(maxs) == 2 else None
    return support_min, support_max


def calendar_violations(
    slices: Sequence[tuple[float, Callable[[float], float]] | CalendarSlice],
    k_grid: tuple[float, ...],
) -> tuple[CalendarViolation, ...]:
    normalized = [
        item if isinstance(item, CalendarSlice) else CalendarSlice(item[0], item[1])
        for item in slices
    ]
    ordered = sorted(normalized, key=lambda item: item.maturity_years)
    violations: list[CalendarViolation] = []
    for short, long in zip(ordered, ordered[1:], strict=False):
        support_min, support_max = _support_intersection(short, long)
        for k in k_grid:
            w_short = short.total_variance(k)
            w_long = long.total_variance(k)
            if w_long < w_short - _CALENDAR_TOL:
                violations.append(
                    CalendarViolation(
                        k=k,
                        maturity_short=short.maturity_years,
                        maturity_long=long.maturity_years,
                        w_short=w_short,
                        w_long=w_long,
                        support_min=support_min,
                        support_max=support_max,
                    )
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
