from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from algotrading.infra.contracts import SurfaceParameters

from .fit import degeneracy_reasons
from .svi import SviParams


@dataclass(frozen=True, slots=True)
class SurfaceSliceSummary:

    expiry_date: date
    maturity_years: float
    method: str
    atm_vol: float
    n_points: int
    rmse: float
    arb_free: bool


def atm_volatility(params: SurfaceParameters) -> float:
    if params.maturity_years <= 0.0:
        return 0.0
    svi = SviParams(
        a=params.svi_a, b=params.svi_b, rho=params.svi_rho, m=params.svi_m, sigma=params.svi_sigma
    )
    total_variance_atm = max(svi.total_variance(0.0), 0.0)
    return math.sqrt(total_variance_atm / params.maturity_years)


def summarize_surface_parameters(
    params: Sequence[SurfaceParameters],
) -> tuple[SurfaceSliceSummary, ...]:
    ordered = sorted(params, key=lambda p: p.maturity_years)
    return tuple(
        SurfaceSliceSummary(
            expiry_date=p.expiry_date,
            maturity_years=p.maturity_years,
            method="svi",
            atm_vol=atm_volatility(p),
            n_points=p.diagnostics.n_points,
            rmse=p.diagnostics.rmse,
            arb_free=p.diagnostics.arb_free,
        )
        for p in ordered
    )


@dataclass(frozen=True, slots=True)
class DenseSurface:

    log_moneyness: tuple[float, ...]
    maturity_years: tuple[float, ...]
    implied_vol: tuple[tuple[float, ...], ...]
    model_version: str
    degenerate_maturity_years: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class ClampedSlice:
    """One fitted SVI slice plus the log-moneyness window it was actually quoted in.

    ``k_lo``/``k_hi`` bound the range of ln(K/F) where this tenor had quotes; the
    clamped reconstruction refuses to evaluate the slice outside that window so the
    dense surface never extrapolates past where the market actually traded.
    """

    maturity_years: float
    params: SviParams
    k_lo: float
    k_hi: float


def _regular_axis(lo: float, hi: float, n: int) -> tuple[float, ...]:
    if n <= 1:
        return (lo,)
    return tuple(lo + (hi - lo) * i / (n - 1) for i in range(n))


def _bracket_interpolate(
    t: float,
    fitted: Sequence[tuple[float, SviParams]],
    k: float,
) -> float:
    """Total variance at ``(k, t)`` interpolated linearly in maturity, flat at the ends.

    Shared by the legacy and clamped reconstruction paths so they cannot drift.
    """
    if t <= fitted[0][0]:
        return max(fitted[0][1].total_variance(k), 0.0)
    if t >= fitted[-1][0]:
        return max(fitted[-1][1].total_variance(k), 0.0)
    for (t_low, p_low), (t_high, p_high) in zip(fitted, fitted[1:], strict=False):
        if t_low <= t <= t_high:
            weight = (t - t_low) / (t_high - t_low)
            w_low, w_high = p_low.total_variance(k), p_high.total_variance(k)
            return max(w_low + weight * (w_high - w_low), 0.0)
    return 0.0  # pragma: no cover - guarded by the range checks above


def reconstruct_dense_surface(
    slices: Sequence[SurfaceParameters],
    *,
    k_min: float = -0.25,
    k_max: float = 0.25,
    n_moneyness: int = 41,
    n_maturities: int = 40,
) -> DenseSurface | None:
    usable = sorted((s for s in slices if s.maturity_years > 0.0), key=lambda s: s.maturity_years)
    if len(usable) < 2:
        return None
    fitted = [
        (
            s.maturity_years,
            SviParams(a=s.svi_a, b=s.svi_b, rho=s.svi_rho, m=s.svi_m, sigma=s.svi_sigma),
        )
        for s in usable
    ]
    t_lo, t_hi = fitted[0][0], fitted[-1][0]
    ks = _regular_axis(k_min, k_max, n_moneyness)
    maturities = _regular_axis(t_lo, t_hi, n_maturities)

    implied_vol = tuple(
        tuple(
            math.sqrt(_bracket_interpolate(t, fitted, k) / t) if t > 0.0 else 0.0 for k in ks
        )
        for t in maturities
    )
    degenerate = tuple(
        sorted(
            {
                s.maturity_years
                for s in usable
                if s.diagnostics is not None and degeneracy_reasons(s.diagnostics)
            }
        )
    )
    return DenseSurface(
        log_moneyness=ks,
        maturity_years=maturities,
        implied_vol=implied_vol,
        model_version=usable[0].model_version,
        degenerate_maturity_years=degenerate,
    )


def reconstruct_dense_surface_clamped(
    slices: Sequence[ClampedSlice],
    *,
    k_min: float = -0.25,
    k_max: float = 0.25,
    n_moneyness: int = 41,
    n_maturities: int = 40,
    model_version: str = "svi",
) -> DenseSurface | None:
    """Dense surface that never extrapolates a slice past its quoted window.

    For each (k, t) cell, total variance is interpolated linearly in maturity
    between the two bracketing slices (flat past the ends), exactly like
    :func:`reconstruct_dense_surface`. The difference is the *clamp*: the quoted
    window ``[k_lo, k_hi]`` is itself interpolated in maturity (flat at the ends),
    and any cell whose ``k`` falls outside ``[k_lo(t), k_hi(t)]`` is a hole.

    Holes are represented as ``float('nan')`` rather than ``None``. This keeps
    ``DenseSurface.implied_vol`` typed as ``tuple[tuple[float, ...], ...]`` and
    needs no serializer change: ``dense_surface_to_dict`` passes cells through
    unchanged, and the frontend ``cleanDenseSurface`` (volRobust.ts) already nulls
    every non-finite / out-of-band cell (``Number.isNaN`` / ``!Number.isFinite``),
    so NaN holes render as gaps, not extrapolated wings.
    """
    usable = sorted(
        (s for s in slices if s.maturity_years > 0.0), key=lambda s: s.maturity_years
    )
    if len(usable) < 2:
        return None
    fitted = [(s.maturity_years, s.params) for s in usable]
    windows = [(s.maturity_years, s.k_lo, s.k_hi) for s in usable]
    t_lo, t_hi = fitted[0][0], fitted[-1][0]
    ks = _regular_axis(k_min, k_max, n_moneyness)
    maturities = _regular_axis(t_lo, t_hi, n_maturities)

    def window_at(t: float) -> tuple[float, float]:
        if t <= windows[0][0]:
            return windows[0][1], windows[0][2]
        if t >= windows[-1][0]:
            return windows[-1][1], windows[-1][2]
        for (t_low, lo_low, hi_low), (t_high, lo_high, hi_high) in zip(
            windows, windows[1:], strict=False
        ):
            if t_low <= t <= t_high:
                weight = (t - t_low) / (t_high - t_low)
                return (
                    lo_low + weight * (lo_high - lo_low),
                    hi_low + weight * (hi_high - hi_low),
                )
        return windows[-1][1], windows[-1][2]  # pragma: no cover - guarded above

    def cell(k: float, t: float, lo: float, hi: float) -> float:
        if t <= 0.0:
            return 0.0
        if k < lo or k > hi:
            return math.nan  # hole: outside this maturity's quoted window
        return math.sqrt(_bracket_interpolate(t, fitted, k) / t)

    implied_vol = tuple(
        tuple(cell(k, t, *window_at(t)) for k in ks) for t in maturities
    )
    # No per-cell diagnostics are carried on a ClampedSlice, so there are no
    # degenerate-maturity flags to surface here; leave the set empty rather than
    # invent flags.
    return DenseSurface(
        log_moneyness=ks,
        maturity_years=maturities,
        implied_vol=implied_vol,
        model_version=model_version,
        degenerate_maturity_years=(),
    )
