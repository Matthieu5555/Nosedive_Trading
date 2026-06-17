"""The per-currency risk-free curve `r(T)` evaluator (ADR 0054, RULED 1–2).

A `RateCurve` is an immutable set of zero-rate pillars `(maturity_years, rate)` in the canonical
continuous-ACT/365 convention. `rate_at(T)` evaluates `r(T)` by **linear interpolation in the zero
rate** between bracketing pillars, with **flat extrapolation** beyond the ends (the shortest
pillar's rate below the first pillar, the longest pillar's rate above the last). A single-pillar
curve is the degenerate flat case — the term-structured generalisation of `ForwardConfig.rate`.

The curve carries no provenance and does no I/O: it is the pure evaluation object that pricing and
the spread diagnostic consume. Building one from persisted `RiskFreeRatePoint` rows (as-of filtered,
no look-ahead) lives in `ingest`/the caller; this module never reads a store.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass


class RateCurveError(ValueError):
    """A risk-free curve cannot be built or evaluated."""


@dataclass(frozen=True, slots=True)
class RatePillar:
    """One zero-rate pillar in canonical continuous-ACT/365 convention."""

    maturity_years: float
    rate: float


@dataclass(frozen=True, slots=True)
class RateCurve:
    """An immutable continuous-ACT/365 zero curve, strictly increasing in `maturity_years`."""

    currency: str
    pillars: tuple[RatePillar, ...]

    def __post_init__(self) -> None:
        if not self.currency.strip():
            raise RateCurveError("currency must be non-empty")
        if not self.pillars:
            raise RateCurveError(
                f"currency {self.currency!r} curve needs at least one pillar (flat is one pillar)"
            )
        last = -math.inf
        for pillar in self.pillars:
            if not (math.isfinite(pillar.maturity_years) and pillar.maturity_years > 0.0):
                raise RateCurveError(
                    f"pillar maturity_years must be finite positive, got {pillar.maturity_years!r}"
                )
            if not math.isfinite(pillar.rate):
                raise RateCurveError(f"pillar rate must be finite, got {pillar.rate!r}")
            if pillar.maturity_years <= last:
                raise RateCurveError(
                    f"currency {self.currency!r} pillars must be strictly increasing in maturity"
                )
            last = pillar.maturity_years

    @classmethod
    def from_pillars(
        cls, currency: str, pillars: Iterable[tuple[float, float]]
    ) -> RateCurve:
        """Build from `(maturity_years, rate)` pairs, sorting by maturity."""
        ordered = sorted(
            (RatePillar(maturity_years=float(t), rate=float(r)) for t, r in pillars),
            key=lambda p: p.maturity_years,
        )
        return cls(currency=currency, pillars=tuple(ordered))

    @classmethod
    def flat(cls, currency: str, rate: float, *, maturity_years: float = 1.0) -> RateCurve:
        """The degenerate single-pillar (flat) curve — `r(T) = rate` for all `T`."""
        return cls(currency=currency, pillars=(RatePillar(maturity_years, rate),))

    def rate_at(self, maturity_years: float) -> float:
        """Evaluate `r(T)`: linear-in-zero-rate interpolation; flat extrapolation past the ends."""
        if not (math.isfinite(maturity_years) and maturity_years > 0.0):
            raise RateCurveError(
                f"maturity_years must be a finite positive year fraction, got {maturity_years!r}"
            )
        pillars = self.pillars
        if maturity_years <= pillars[0].maturity_years:
            return pillars[0].rate
        if maturity_years >= pillars[-1].maturity_years:
            return pillars[-1].rate
        lo, hi = _bracket(pillars, maturity_years)
        span = hi.maturity_years - lo.maturity_years
        weight = (maturity_years - lo.maturity_years) / span
        return lo.rate + weight * (hi.rate - lo.rate)

    def discount_factor(self, maturity_years: float) -> float:
        """`exp(-r(T) * T)` — continuous ACT/365 discount factor at the evaluated rate."""
        return math.exp(-self.rate_at(maturity_years) * maturity_years)


def _bracket(pillars: Sequence[RatePillar], maturity_years: float) -> tuple[RatePillar, RatePillar]:
    # pillars is strictly increasing and maturity_years is strictly interior here.
    for left, right in zip(pillars, pillars[1:], strict=False):
        if left.maturity_years <= maturity_years <= right.maturity_years:
            return left, right
    raise RateCurveError(  # pragma: no cover - guarded by interior check in rate_at
        f"no bracketing pillars for maturity {maturity_years!r}"
    )
