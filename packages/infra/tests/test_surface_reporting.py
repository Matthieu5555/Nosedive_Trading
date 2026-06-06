"""Tests for `surfaces.reporting` — the surface summary the CLI/API renders.

The summary's one derived number is the at-the-money volatility; it is asserted against a
value hand-computed from the SVI formula, never read back from the code under test. The
ordering and diagnostic pass-through are pinned too.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime

import pytest
from algotrading.core.provenance import stamp
from algotrading.infra.contracts import SurfaceFitDiagnostics, SurfaceParameters
from algotrading.infra.surfaces import summarize_surface_parameters
from algotrading.infra.surfaces.reporting import atm_volatility

_TS = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)


def _params(
    *,
    maturity_years: float,
    a: float,
    b: float,
    rho: float,
    m: float,
    sigma: float,
    expiry: date,
    n_points: int = 7,
    rmse: float = 1e-4,
    arb_free: bool = True,
) -> SurfaceParameters:
    a_stamp = stamp(
        calc_ts=_TS, code_version="surface-1", config_hashes={"cfg": "c"},
        source_records=(), source_timestamps=(),
    )
    return SurfaceParameters(
        snapshot_ts=_TS, underlying="SPY", maturity_years=maturity_years, model_version="surface-1",
        svi_a=a, svi_b=b, svi_rho=rho, svi_m=m, svi_sigma=sigma,
        expiry_date=expiry, day_count="ACT/365",
        diagnostics=SurfaceFitDiagnostics(rmse=rmse, n_points=n_points, arb_free=arb_free),
        source_snapshot_ts=_TS, provenance=a_stamp,
    )


def test_atm_volatility_matches_the_hand_computed_svi_value() -> None:
    # w(0) = a + b*(rho*(0-m) + sqrt((0-m)^2 + sigma^2)). With m=0: w(0) = a + b*sigma.
    # a=0.04, b=0.10, sigma=0.20 -> w(0) = 0.04 + 0.10*0.20 = 0.06.
    # atm_vol = sqrt(w(0)/T) = sqrt(0.06/0.25) = sqrt(0.24) = 0.4898979485566356.
    params = _params(maturity_years=0.25, a=0.04, b=0.10, rho=-0.5, m=0.0, sigma=0.20,
                     expiry=date(2026, 6, 19))
    assert atm_volatility(params) == pytest.approx(math.sqrt(0.24), rel=1e-12)


def test_atm_volatility_is_zero_for_a_nonpositive_maturity() -> None:
    # An expired/same-day slice has no annualization; the summary must not divide by zero.
    params = _params(maturity_years=0.0, a=0.04, b=0.10, rho=-0.5, m=0.0, sigma=0.20,
                     expiry=date(2026, 5, 29))
    assert atm_volatility(params) == 0.0


def test_summarize_orders_by_maturity_and_passes_diagnostics_through() -> None:
    far = _params(maturity_years=0.50, a=0.05, b=0.10, rho=-0.4, m=0.0, sigma=0.20,
                  expiry=date(2026, 9, 18), n_points=9, rmse=2e-4, arb_free=False)
    near = _params(maturity_years=0.10, a=0.03, b=0.10, rho=-0.6, m=0.0, sigma=0.20,
                   expiry=date(2026, 6, 19), n_points=6, rmse=1e-4, arb_free=True)

    summaries = summarize_surface_parameters([far, near])

    # Sorted shortest maturity first.
    assert [s.maturity_years for s in summaries] == [0.10, 0.50]
    near_summary, far_summary = summaries
    # Diagnostics copied verbatim from each slice.
    assert (near_summary.n_points, near_summary.rmse, near_summary.arb_free) == (6, 1e-4, True)
    assert (far_summary.n_points, far_summary.rmse, far_summary.arb_free) == (9, 2e-4, False)
    assert near_summary.expiry_date == date(2026, 6, 19)
    assert near_summary.method == "svi"
    # near: w(0) = 0.03 + 0.10*0.20 = 0.05; atm_vol = sqrt(0.05/0.10) = sqrt(0.5).
    assert near_summary.atm_vol == pytest.approx(math.sqrt(0.5), rel=1e-12)


def test_summarize_empty_is_empty() -> None:
    assert summarize_surface_parameters([]) == ()
