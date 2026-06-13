"""Tests for ``reconstruct_dense_surface`` (the blueprint's reconstructed surface grid).

Oracle, never the code under test: the raw-SVI total variance
``w(k) = a + b(rho*(k-m) + sqrt((k-m)^2 + sigma^2))`` (Eq 20) computed by hand here, and the
calendar-consistent cross-maturity rule ``w`` is linear *in variance* between bracketing slices
(Eq 22). Two slices with known parameters let us pin both the per-slice sampling and the
mid-maturity interpolation; a flagged slice pins the degeneracy passthrough.
"""

from __future__ import annotations

import math

import pytest
from algotrading.infra.contracts import SurfaceParameters
from algotrading.infra.contracts.bundles import SurfaceFitDiagnostics
from algotrading.infra.surfaces import reconstruct_dense_surface
from fixtures.records import EXPIRY, SNAPSHOT_TS, make_stamp


def _svi_w(k: float, a: float, b: float, rho: float, m: float, sigma: float) -> float:
    """Raw-SVI total variance (Eq 20) — the independent oracle."""
    x = k - m
    return a + b * (rho * x + math.sqrt(x * x + sigma * sigma))


def _slice(
    maturity_years: float,
    *,
    a: float,
    b: float,
    rho: float,
    m: float,
    sigma: float,
    arb_free: bool = True,
) -> SurfaceParameters:
    return SurfaceParameters(
        snapshot_ts=SNAPSHOT_TS,
        underlying="AAA",
        maturity_years=maturity_years,
        model_version="svi-test",
        svi_a=a,
        svi_b=b,
        svi_rho=rho,
        svi_m=m,
        svi_sigma=sigma,
        expiry_date=EXPIRY,
        day_count="ACT/365",
        diagnostics=SurfaceFitDiagnostics(
            rmse=0.001, n_points=8, arb_free=arb_free, bound_hits=(), converged=True
        ),
        source_snapshot_ts=SNAPSHOT_TS,
        provenance=make_stamp(),
    )


# Two clean slices with known parameters (ATM vertex w(0) = a + b*sigma at m = 0).
_P1 = dict(a=0.04, b=0.10, rho=-0.30, m=0.0, sigma=0.20)  # T = 0.25
_P2 = dict(a=0.06, b=0.12, rho=-0.30, m=0.0, sigma=0.20)  # T = 1.00


def test_grid_shape_and_axes() -> None:
    surface = reconstruct_dense_surface(
        [_slice(0.25, **_P1), _slice(1.0, **_P2)], n_moneyness=5, n_maturities=3
    )
    assert surface is not None
    assert surface.log_moneyness == pytest.approx([-0.25, -0.125, 0.0, 0.125, 0.25])
    assert surface.maturity_years == pytest.approx([0.25, 0.625, 1.0])  # linspace(T_lo, T_hi)
    assert len(surface.implied_vol) == 3 and all(len(row) == 5 for row in surface.implied_vol)
    assert surface.model_version == "svi-test"


def test_endpoint_rows_are_each_slice_sampled() -> None:
    # At a fitted maturity the row is exactly that slice's curve: IV(k) = sqrt(w(k)/T).
    surface = reconstruct_dense_surface(
        [_slice(0.25, **_P1), _slice(1.0, **_P2)], n_moneyness=5, n_maturities=3
    )
    assert surface is not None
    for j, k in enumerate(surface.log_moneyness):
        assert surface.implied_vol[0][j] == pytest.approx(math.sqrt(_svi_w(k, **_P1) / 0.25))
        assert surface.implied_vol[2][j] == pytest.approx(math.sqrt(_svi_w(k, **_P2) / 1.0))


def test_mid_maturity_interpolates_linearly_in_variance() -> None:
    # Eq 22: the 0.625y row (midway between 0.25 and 1.0) is the variance midpoint, NOT the IV
    # midpoint — w_mid = (w1 + w2)/2, then IV = sqrt(w_mid / T).
    surface = reconstruct_dense_surface(
        [_slice(0.25, **_P1), _slice(1.0, **_P2)], n_moneyness=5, n_maturities=3
    )
    assert surface is not None
    for j, k in enumerate(surface.log_moneyness):
        w_mid = 0.5 * (_svi_w(k, **_P1) + _svi_w(k, **_P2))
        assert surface.implied_vol[1][j] == pytest.approx(math.sqrt(w_mid / 0.625))


def test_degenerate_maturities_are_flagged_not_hidden() -> None:
    # An arb-breaching slice is reported in degenerate_maturity_years (blueprint: flag, never
    # silently serve as clean); the clean slice is not.
    surface = reconstruct_dense_surface(
        [_slice(0.25, **_P1), _slice(1.0, **_P2, arb_free=False)], n_moneyness=5, n_maturities=3
    )
    assert surface is not None
    assert surface.degenerate_maturity_years == pytest.approx([1.0])


def test_fewer_than_two_slices_is_not_a_surface() -> None:
    # A single slice is a smile, not a surface — the caller falls back to its sparse view.
    assert reconstruct_dense_surface([_slice(0.25, **_P1)]) is None
    assert reconstruct_dense_surface([]) is None


def test_non_positive_maturity_slices_are_dropped() -> None:
    # An expired/same-day slice (T <= 0) cannot be annualized and is excluded; with only one
    # positive-maturity slice left, there is no surface.
    assert (
        reconstruct_dense_surface([_slice(0.0, **_P1), _slice(0.25, **_P2)]) is None
    )
