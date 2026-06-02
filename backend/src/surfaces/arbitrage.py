"""Static no-arbitrage diagnostics for a fitted surface.

Two conditions, both reported (not enforced — a fit that violates them is *labeled*,
so a consumer can see and decide):

* **Calendar (Eq 21).** Total variance must not fall as maturity rises, at a fixed
  log-moneyness: ``w(k, T2) >= w(k, T1)`` for ``T2 > T1``. A dip means a negative
  forward variance — a calendar-spread arbitrage.

* **Butterfly.** The risk-neutral density implied by one slice must be non-negative.
  Gatheral's function expresses this directly from the total-variance smile and its
  derivatives:

      g(k) = (1 - k w' / (2 w))^2 - (w' / 2)^2 (1/w + 1/4) + w'' / 2

  ``g(k) >= 0`` everywhere is exactly the no-butterfly condition; a negative value is
  a breach. We read ``w``, ``w'``, ``w''`` from the closed-form SVI derivatives, so
  the check is exact, not finite-differenced.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from .svi import SviParams

# A small negative slack so float noise on an exactly-flat boundary is not flagged.
_CALENDAR_TOL = 1e-9
_BUTTERFLY_TOL = 1e-9


@dataclass(frozen=True, slots=True)
class CalendarViolation:
    """One log-moneyness where total variance fell as maturity rose."""

    k: float
    maturity_short: float
    maturity_long: float
    w_short: float
    w_long: float


def calendar_violations(
    slices: Sequence[tuple[float, Callable[[float], float]]],
    k_grid: tuple[float, ...],
) -> tuple[CalendarViolation, ...]:
    """Find ``(k, T)`` where total variance decreases with maturity (Eq 21).

    ``slices`` is ``(maturity_years, total_variance_fn)`` pairs; they are sorted by
    maturity here, then every adjacent pair is checked across ``k_grid``. A breach is
    ``w_long < w_short - tol``. Returns one :class:`CalendarViolation` per offending
    ``(k, adjacent-pair)``; an empty tuple means calendar-arbitrage-free on the grid.
    """
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
    """Gatheral's ``g(k)``; non-negative iff the slice is butterfly-arbitrage-free."""
    w = params.total_variance(k)
    w1 = params.first_derivative(k)
    w2 = params.second_derivative(k)
    term_1 = (1.0 - k * w1 / (2.0 * w)) ** 2
    term_2 = (w1 / 2.0) ** 2 * (1.0 / w + 0.25)
    return term_1 - term_2 + w2 / 2.0


def butterfly_violations(params: SviParams, k_grid: tuple[float, ...]) -> tuple[float, ...]:
    """Log-moneyness points on ``k_grid`` where ``g(k) < 0`` (a butterfly breach).

    An empty tuple means the slice is butterfly-arbitrage-free across the grid. A
    non-positive total variance anywhere is itself an arbitrage and is reported as a
    breach at that ``k``.
    """
    violations: list[float] = []
    for k in k_grid:
        if params.total_variance(k) <= 0.0 or butterfly_g(params, k) < -_BUTTERFLY_TOL:
            violations.append(k)
    return tuple(violations)
