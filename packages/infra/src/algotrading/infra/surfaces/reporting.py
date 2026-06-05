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
