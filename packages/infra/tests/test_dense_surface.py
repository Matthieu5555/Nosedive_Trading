from __future__ import annotations

import math

import pytest
from algotrading.infra.contracts import SurfaceParameters
from algotrading.infra.contracts.bundles import SurfaceFitDiagnostics
from algotrading.infra.surfaces import reconstruct_dense_surface
from algotrading.infra.surfaces.reporting import (
    FILLED_IV_CAP,
    ClampedSlice,
    reconstruct_dense_surface_clamped,
)
from algotrading.infra.surfaces.svi import SviParams
from fixtures.records import EXPIRY, SNAPSHOT_TS, make_stamp


def _svi_w(k: float, a: float, b: float, rho: float, m: float, sigma: float) -> float:
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


_P1 = dict(a=0.04, b=0.10, rho=-0.30, m=0.0, sigma=0.20)
_P2 = dict(a=0.06, b=0.12, rho=-0.30, m=0.0, sigma=0.20)


def test_grid_shape_and_axes() -> None:
    surface = reconstruct_dense_surface(
        [_slice(0.25, **_P1), _slice(1.0, **_P2)], n_moneyness=5, n_maturities=3
    )
    assert surface is not None
    assert surface.log_moneyness == pytest.approx([-0.25, -0.125, 0.0, 0.125, 0.25])
    assert surface.maturity_years == pytest.approx([0.25, 0.625, 1.0])
    assert len(surface.implied_vol) == 3 and all(len(row) == 5 for row in surface.implied_vol)
    assert surface.model_version == "svi-test"


def test_endpoint_rows_are_each_slice_sampled() -> None:
    surface = reconstruct_dense_surface(
        [_slice(0.25, **_P1), _slice(1.0, **_P2)], n_moneyness=5, n_maturities=3
    )
    assert surface is not None
    for j, k in enumerate(surface.log_moneyness):
        assert surface.implied_vol[0][j] == pytest.approx(math.sqrt(_svi_w(k, **_P1) / 0.25))
        assert surface.implied_vol[2][j] == pytest.approx(math.sqrt(_svi_w(k, **_P2) / 1.0))


def test_mid_maturity_interpolates_linearly_in_variance() -> None:
    surface = reconstruct_dense_surface(
        [_slice(0.25, **_P1), _slice(1.0, **_P2)], n_moneyness=5, n_maturities=3
    )
    assert surface is not None
    for j, k in enumerate(surface.log_moneyness):
        w_mid = 0.5 * (_svi_w(k, **_P1) + _svi_w(k, **_P2))
        assert surface.implied_vol[1][j] == pytest.approx(math.sqrt(w_mid / 0.625))


def test_degenerate_maturities_are_flagged_not_hidden() -> None:
    surface = reconstruct_dense_surface(
        [_slice(0.25, **_P1), _slice(1.0, **_P2, arb_free=False)], n_moneyness=5, n_maturities=3
    )
    assert surface is not None
    assert surface.degenerate_maturity_years == pytest.approx([1.0])


def test_fewer_than_two_slices_is_not_a_surface() -> None:
    assert reconstruct_dense_surface([_slice(0.25, **_P1)]) is None
    assert reconstruct_dense_surface([]) is None


def test_non_positive_maturity_slices_are_dropped() -> None:
    assert (
        reconstruct_dense_surface([_slice(0.0, **_P1), _slice(0.25, **_P2)]) is None
    )


# --- clamped reconstruction (never extrapolate past the quoted window) -----------------------------


def _clamped(maturity_years: float, params: dict, k_lo: float, k_hi: float) -> ClampedSlice:
    return ClampedSlice(
        maturity_years=maturity_years,
        params=SviParams(**params),
        k_lo=k_lo,
        k_hi=k_hi,
    )


def test_clamped_holes_outside_quoted_window_are_nan_not_extrapolated() -> None:
    # Both slices quoted only in a narrow window; the dense grid spans [-0.25, 0.25].
    surface = reconstruct_dense_surface_clamped(
        [
            _clamped(0.25, _P1, k_lo=-0.05, k_hi=0.05),
            _clamped(1.0, _P2, k_lo=-0.05, k_hi=0.05),
        ],
        n_moneyness=5,  # k = -0.25, -0.125, 0.0, 0.125, 0.25
        n_maturities=3,
    )
    assert surface is not None
    # Edge columns (|k| = 0.25, 0.125) are outside the window -> NaN holes, not exploded wings.
    for row in surface.implied_vol:
        assert math.isnan(row[0])  # k = -0.25
        assert math.isnan(row[1])  # k = -0.125
        assert math.isnan(row[3])  # k = +0.125
        assert math.isnan(row[4])  # k = +0.25
        # The in-window centre column is a finite, sane vol (no extrapolation blowup).
        assert math.isfinite(row[2])  # k = 0.0
        assert 0.0 < row[2] < 1.0


def test_clamped_in_window_cells_match_svi_evaluation() -> None:
    surface = reconstruct_dense_surface_clamped(
        [
            _clamped(0.25, _P1, k_lo=-0.25, k_hi=0.25),
            _clamped(1.0, _P2, k_lo=-0.25, k_hi=0.25),
        ],
        n_moneyness=5,
        n_maturities=3,
    )
    assert surface is not None
    for j, k in enumerate(surface.log_moneyness):
        # Endpoint rows are each slice sampled directly.
        assert surface.implied_vol[0][j] == pytest.approx(math.sqrt(_svi_w(k, **_P1) / 0.25))
        assert surface.implied_vol[2][j] == pytest.approx(math.sqrt(_svi_w(k, **_P2) / 1.0))
        # Mid maturity interpolates total variance linearly, same as the legacy path.
        w_mid = 0.5 * (_svi_w(k, **_P1) + _svi_w(k, **_P2))
        assert surface.implied_vol[1][j] == pytest.approx(math.sqrt(w_mid / 0.625))


def test_clamped_window_interpolates_in_maturity() -> None:
    # Near slice narrow, far slice wide -> at mid maturity the window is the average.
    surface = reconstruct_dense_surface_clamped(
        [
            _clamped(0.25, _P1, k_lo=-0.05, k_hi=0.05),
            _clamped(1.0, _P2, k_lo=-0.25, k_hi=0.25),
        ],
        n_moneyness=5,  # k = -0.25, -0.125, 0.0, 0.125, 0.25
        n_maturities=3,  # t = 0.25, 0.625, 1.0
    )
    assert surface is not None
    near, mid, far = surface.implied_vol
    # Near slice: only k=0.0 is inside [-0.05, 0.05].
    assert math.isnan(near[1]) and math.isfinite(near[2]) and math.isnan(near[3])
    # Mid (t=0.625) window interpolates to [-0.15, 0.15]: |k|=0.125 now inside, |k|=0.25 still out.
    assert math.isfinite(mid[1]) and math.isfinite(mid[3])
    assert math.isnan(mid[0]) and math.isnan(mid[4])
    # Far slice: full window, every column finite.
    assert all(math.isfinite(v) for v in far)


def test_clamped_fewer_than_two_slices_is_none() -> None:
    assert reconstruct_dense_surface_clamped([_clamped(0.25, _P1, -0.05, 0.05)]) is None
    assert reconstruct_dense_surface_clamped([]) is None
    # Non-positive maturities are dropped, leaving < 2 usable.
    assert (
        reconstruct_dense_surface_clamped(
            [_clamped(0.0, _P1, -0.05, 0.05), _clamped(0.25, _P2, -0.05, 0.05)]
        )
        is None
    )


def test_clamped_no_degenerate_flags_invented() -> None:
    surface = reconstruct_dense_surface_clamped(
        [_clamped(0.25, _P1, -0.05, 0.05), _clamped(1.0, _P2, -0.05, 0.05)]
    )
    assert surface is not None
    assert surface.degenerate_maturity_years == ()


# --- filled ("clean") z-grid: fully filled edge-to-edge and capped -----------------------------


def test_filled_grid_has_no_holes_while_clamped_grid_does() -> None:
    # Narrow quoted window -> clamped grid holes the wings, filled grid fills them.
    surface = reconstruct_dense_surface_clamped(
        [
            _clamped(0.25, _P1, k_lo=-0.05, k_hi=0.05),
            _clamped(1.0, _P2, k_lo=-0.05, k_hi=0.05),
        ],
        n_moneyness=5,
        n_maturities=3,
    )
    assert surface is not None
    # Filled grid is fully filled: no NaN / non-finite anywhere.
    assert all(math.isfinite(v) for row in surface.implied_vol_filled for v in row)
    # Same shape as the clamped grid.
    assert len(surface.implied_vol_filled) == len(surface.implied_vol)
    assert all(
        len(f) == len(c)
        for f, c in zip(surface.implied_vol_filled, surface.implied_vol, strict=True)
    )
    # The clamped grid still holes outside the window (wings are NaN).
    assert any(math.isnan(v) for row in surface.implied_vol for v in row)


def test_filled_grid_caps_blown_up_wings_at_FILLED_IV_CAP() -> None:
    # A short tenor with a steep, badly-constrained smile: the unclamped SVI wing
    # IV exceeds 0.60. The filled grid must clamp it to exactly the cap, not null,
    # not larger.
    blown = dict(a=0.02, b=2.0, rho=0.0, m=0.0, sigma=0.01)
    short = 0.05
    # Sanity: the raw SVI wing IV really would exceed the cap (otherwise the test
    # proves nothing).
    raw_wing_iv = math.sqrt(_svi_w(0.25, **blown) / short)
    assert raw_wing_iv > FILLED_IV_CAP

    surface = reconstruct_dense_surface_clamped(
        [
            _clamped(short, blown, k_lo=-0.25, k_hi=0.25),
            _clamped(1.0, _P2, k_lo=-0.25, k_hi=0.25),
        ],
        n_moneyness=5,  # k includes the +/-0.25 wings
        n_maturities=3,
    )
    assert surface is not None
    # Every finite filled cell is <= the cap, and none are NaN.
    for row in surface.implied_vol_filled:
        for v in row:
            assert math.isfinite(v)
            assert v <= FILLED_IV_CAP + 1e-12
    # The short-tenor wing specifically is clamped to exactly the cap.
    near = surface.implied_vol_filled[0]
    assert near[0] == pytest.approx(FILLED_IV_CAP)  # k = -0.25
    assert near[4] == pytest.approx(FILLED_IV_CAP)  # k = +0.25


def test_filled_and_clamped_agree_inside_quoted_window_below_cap() -> None:
    # Sane slices fully quoted across the grid; nothing hits the cap, so the filled
    # grid equals the clamped grid cell-for-cell.
    surface = reconstruct_dense_surface_clamped(
        [
            _clamped(0.25, _P1, k_lo=-0.25, k_hi=0.25),
            _clamped(1.0, _P2, k_lo=-0.25, k_hi=0.25),
        ],
        n_moneyness=5,
        n_maturities=3,
    )
    assert surface is not None
    for fr, cr in zip(surface.implied_vol_filled, surface.implied_vol, strict=True):
        for fv, cv in zip(fr, cr, strict=True):
            assert math.isfinite(cv)  # window is full here, so clamped is finite
            assert fv < FILLED_IV_CAP  # below cap, so untouched by the clamp
            assert fv == pytest.approx(cv)


def test_filled_agrees_with_clamped_only_in_window_when_window_narrow() -> None:
    # Narrow window: where the clamped grid is finite (inside the window) and below
    # the cap, the filled grid must match it exactly. Outside the window the clamped
    # grid is NaN but the filled grid is still a finite (possibly capped) value.
    surface = reconstruct_dense_surface_clamped(
        [
            _clamped(0.25, _P1, k_lo=-0.05, k_hi=0.05),
            _clamped(1.0, _P2, k_lo=-0.05, k_hi=0.05),
        ],
        n_moneyness=5,
        n_maturities=3,
    )
    assert surface is not None
    for fr, cr in zip(surface.implied_vol_filled, surface.implied_vol, strict=True):
        for fv, cv in zip(fr, cr, strict=True):
            if math.isfinite(cv) and cv < FILLED_IV_CAP:
                assert fv == pytest.approx(cv)
            else:
                # Holed (or capped) in the clamped grid; filled is finite regardless.
                assert math.isfinite(fv)
