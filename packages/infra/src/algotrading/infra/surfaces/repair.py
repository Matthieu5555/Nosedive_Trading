"""Monotone total-variance repair: a calendar-arbitrage floor on the surface (ADR 0062).

Calendar no-arbitrage requires total implied variance ``w(k, T) = sigma(k, T)^2 * T`` to be
non-decreasing in maturity ``T`` at a fixed log-moneyness ``k``. Falling *IV* with maturity is
fine (a downward term structure); what is forbidden is falling *total variance*, because variance
accumulates additively and the extra calendar time cannot subtract uncertainty.

Each expiry's smile is fit independently (see :func:`..fit.fit_slice`). In the extrapolated wings,
outside the strikes actually quoted, two independent smiles can cross, producing a
``w(k, T_long) < w(k, T_short)`` inversion of two numbers the model invented with no market behind
them. ADR 0061 already stops that from *paging* (it is downgraded to a non-blocking notice when it
sits outside observed strike support). This module is the served-surface counterpart: it repairs
those extrapolated marks so the stored grid is calendar-arbitrage-free, while never rewriting a
traded mark and never silently masking a genuine in-support inversion.

The repair walks expiries short -> long within each underlying and, at every grid point ``k``:

* **inside** the slice's observed strike support: keep the raw fit untouched. A real, in-data
  calendar inversion is left exactly as fit so the QC still pages on it (it is a true signal, not
  a model artefact).
* **outside** the support (the model is extrapolating): clamp the value up to the prior repaired
  curve so ``w`` cannot fall below the shorter expiry. The floor chains, so a long wing is pinned
  to the nearest shorter wing rather than to two-expiries-ago.

The result is a discrete lookup on the same moneyness grid the surface is projected onto, applied
identically to the served ``surface_grid`` and to the calendar QC so the stored surface and its
check agree. It is gated default-off (``SurfaceConfig.calendar_variance_repair``); flipping it on
changes served marks, so it is a hashed config behaviour, never a silent default.

This is the contained first step. It repairs the discrete served grid, not the continuous SVI
parameters (off-grid evaluation, e.g. live risk on an arbitrary strike, still reads the raw fit).
The deeper fix that makes the curves non-crossing by construction (calendar-coupled SVI / SSVI)
is the deferred ADR-0062 follow-up, because it rewrites surface levels across every name and needs
a visual review before it can be trusted.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

DEFAULT_SUPPORT_EPSILON = 1e-6


@dataclass(frozen=True, slots=True)
class RepairSlice:
    """One expiry's curve as the floor sees it: a maturity, a ``w(k)`` callable, and its support."""

    underlying: str
    maturity_years: float
    total_variance: Callable[[float], float]
    observed_k_min: float | None
    observed_k_max: float | None


def _within_support(
    k: float, k_min: float | None, k_max: float | None, epsilon: float
) -> bool:
    """True when ``k`` lies inside the observed strike envelope (real data, never repaired).

    A slice with no observed support (``None`` bounds) is treated as everywhere-extrapolated, so
    its wings can be floored; it has no traded marks to protect.
    """
    if k_min is None or k_max is None:
        return False
    return (k_min - epsilon) <= k <= (k_max + epsilon)


def monotone_variance_floor(
    slices: Sequence[RepairSlice],
    k_grid: Sequence[float],
    *,
    support_epsilon: float = DEFAULT_SUPPORT_EPSILON,
) -> dict[tuple[str, float], dict[float, float]]:
    """Repaired total variance per ``(underlying, maturity_years)``, evaluated on ``k_grid``.

    See the module docstring for the policy. Returns a nested lookup
    ``{(underlying, maturity_years): {k: w}}`` covering exactly the grid points; callers apply it
    to both the served grid projection and the calendar QC. Slices are grouped by underlying and
    walked in ascending maturity; the shortest expiry of each underlying is never floored.
    """
    by_underlying: dict[str, list[RepairSlice]] = {}
    for item in slices:
        by_underlying.setdefault(item.underlying, []).append(item)

    grid = tuple(k_grid)
    repaired: dict[tuple[str, float], dict[float, float]] = {}
    for underlying in sorted(by_underlying):
        ordered = sorted(by_underlying[underlying], key=lambda item: item.maturity_years)
        floor: dict[float, float] = {}
        for slice_ in ordered:
            curve: dict[float, float] = {}
            for k in grid:
                raw = slice_.total_variance(k)
                prior = floor.get(k)
                if prior is None or _within_support(
                    k, slice_.observed_k_min, slice_.observed_k_max, support_epsilon
                ):
                    value = raw
                else:
                    value = prior if raw < prior else raw
                curve[k] = value
                floor[k] = value
            repaired[(underlying, slice_.maturity_years)] = curve
    return repaired


def repair_overrides_from_slices(
    slices: Sequence[RepairSlice],
    k_grid: Sequence[float],
    *,
    enabled: bool,
    support_epsilon: float = DEFAULT_SUPPORT_EPSILON,
) -> Mapping[tuple[str, float], dict[float, float]]:
    """Convenience gate: the floor when ``enabled``, an empty map (raw surface) otherwise."""
    if not enabled:
        return {}
    return monotone_variance_floor(slices, k_grid, support_epsilon=support_epsilon)
