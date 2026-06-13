"""Summarize a fitted surface into rows ready to print, assert, or serialize.

The reporting layer between the persisted :class:`~contracts.SurfaceParameters` contract
and whatever displays it — a CLI table, a test assertion, an API response. It computes
the one derived quantity an operator reads first, the at-the-money volatility, *from* the
calibrated SVI parameters rather than from any grid cell, so the headline number traces
straight to the persisted model. Pure: it builds structured rows and renders no output of
its own, leaving formatting to the caller.

ATM volatility is ``sqrt(w(0) / T)``, where ``w(0)`` is the SVI total variance at
log-moneyness ``k = 0`` (the forward). That total variance is read off the same
:class:`~surfaces.svi.SviParams.total_variance` the calibration and grid use, so the
summary cannot drift from the curve it describes.
"""

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
    """One maturity slice reduced to the numbers a reader wants at a glance.

    ``method`` is always ``"svi"`` here — only SVI slices persist
    :class:`~contracts.SurfaceParameters` — but it is carried explicitly so the row reads
    the same as a future nonparametric summary would. ``atm_vol`` is the annualized
    at-the-money volatility read off the fitted smile; ``arb_free`` is the slice's
    butterfly verdict.
    """

    expiry_date: date
    maturity_years: float
    method: str
    atm_vol: float
    n_points: int
    rmse: float
    arb_free: bool


def atm_volatility(params: SurfaceParameters) -> float:
    """Annualized at-the-money volatility of one fitted slice: ``sqrt(w(0) / T)``.

    ``w(0)`` is the SVI total variance at the forward (``k = 0``), reconstructed from the
    persisted parameters via :meth:`~surfaces.svi.SviParams.total_variance` — the same
    evaluation the calibration uses, so this never disagrees with the stored curve. A
    non-positive maturity (an expired or same-day slice) has no annualization and yields
    ``0.0`` rather than dividing by zero.
    """
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
    """Reduce persisted SVI parameters into per-maturity summary rows, sorted by maturity.

    One :class:`SurfaceSliceSummary` per input slice, ordered shortest maturity first so a
    rendered table reads front-to-back along the term structure. Each row's ATM vol is
    derived from that slice's own parameters; the diagnostic fields are copied verbatim.
    """
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
    """A regularized vol surface reconstructed from the fitted SVI slices, ready to render.

    The blueprint's queryable surface grid (05-math-notes §"reconstructed grid", glossary
    "Surface grid"): a dense ``(maturity × log-moneyness)`` lattice of *implied vol*, sampled
    from the persisted SVI parameters rather than served as the sparse delta-band points a
    smile is fitted from — so a 3D surface reads as the smooth fitted model, not a coarse
    polyline. ``implied_vol[i][j]`` is the vol at ``maturity_years[i]`` and
    ``log_moneyness[j]``. ``degenerate_maturity_years`` carries the fitted maturities whose
    calibration is flagged (see :func:`degeneracy_reasons`) so the caller can surface the
    caveat instead of hiding it (blueprint: flag, never silently serve as clean).
    """

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
    """Reconstruct a dense implied-vol surface from persisted SVI slices, or ``None``.

    Samples each slice's curve ``w(k)`` on an ``n_moneyness``-point log-moneyness grid, then
    interpolates *across* maturities **in total-variance space** (blueprint Eq 22: flat outside
    the fitted range, linear in ``w`` between the two bracketing slices) onto an
    ``n_maturities``-point maturity axis. Implied vol is ``sqrt(w / T)`` per cell. Returns
    ``None`` when fewer than two positive-maturity slices are available — a single slice is a
    smile, not a surface, and the caller should fall back to its sparse view.
    """
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
        # Eq 22: hold the nearest slice flat outside the fitted range, linear in w inside.
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
