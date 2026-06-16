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
    ks = tuple(k_min + (k_max - k_min) * i / (n_moneyness - 1) for i in range(n_moneyness))
    maturities = tuple(t_lo + (t_hi - t_lo) * j / (n_maturities - 1) for j in range(n_maturities))

    def total_variance_at(k: float, t: float) -> float:
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

    implied_vol = tuple(
        tuple(math.sqrt(total_variance_at(k, t) / t) if t > 0.0 else 0.0 for k in ks)
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
