"""Tests for the surface engine (step 9).

Independent oracles, never the code under test:

* ``fixtures.synthetic`` generates total-variance points from *known* SVI parameters
  (a=0.04, b=0.10, rho=-0.30, m=0, sigma=0.20). The generator is the oracle: the
  fitter must recover those parameters and reproduce the points.
* By-hand SVI values: at ``m=0`` the vertex is ``w(0) = a + b*sigma``.
* By-hand calendar and butterfly fixtures constructed to violate each condition.
* Calendar no-arb property: a flat forward-variance term structure has total variance
  ``w(k, T) = base(k) * T`` with ``base(k) >= 0``, so ``w`` is non-decreasing in
  maturity by construction — an oracle independent of the surface code.

Float comparisons use explicit tolerances sized to each oracle.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from contracts import (
    IvDiagnostics,
    IvPoint,
    SurfaceGrid,
    SurfaceParameters,
    table_for_contract,
    validate,
)
from fixtures.synthetic import build_synthetic_surface, svi_total_variance
from provenance import source_ref, stamp
from surfaces import (
    METHOD_INSUFFICIENT,
    METHOD_NONPARAMETRIC,
    METHOD_SVI,
    MIN_POINTS_FOR_SVI,
    SliceFit,
    SviParams,
    butterfly_g,
    butterfly_violations,
    calendar_violations,
    fit_slice,
    fit_svi,
    interpolate_total_variance,
    slice_plot_series,
    surface_grid_cells,
    surface_parameters,
)

TS = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)
EXPIRY = date(2026, 6, 19)
_SURFACE = build_synthetic_surface()  # F=100, DF=0.99, T=0.25
_TRUE = (_SURFACE.svi_a, _SURFACE.svi_b, _SURFACE.svi_rho, _SURFACE.svi_m, _SURFACE.svi_sigma)
_CALENDAR_K_GRID = (-0.4, -0.2, 0.0, 0.2, 0.4)  # log-moneyness probed by the calendar property


def _iv_point(k: float, w: float, key: str) -> IvPoint:
    """A minimal valid IvPoint carrying log-moneyness k and total variance w."""
    a_stamp = stamp(
        calc_ts=TS, code_version="iv-1", config_hash="c",
        source_records=(source_ref("market_state_snapshots", TS, key),), source_timestamps=(TS,),
    )
    iv = math.sqrt(w / _SURFACE.maturity_years) if w > 0 else 0.0
    return IvPoint(
        snapshot_ts=TS, contract_key=key, iv=iv, k=k, total_variance=w, solver_version="iv-1",
        diagnostics=IvDiagnostics(converged=True, iterations=5, residual=1e-12, status="converged"),
        source_snapshot_ts=TS, provenance=a_stamp,
    )


def _synthetic_points() -> tuple[IvPoint, ...]:
    return tuple(
        _iv_point(p.log_moneyness, p.total_variance, f"K{p.strike:g}") for p in _SURFACE.points
    )


def _fit(points: tuple[IvPoint, ...], *, maturity: float = _SURFACE.maturity_years,
         expiry: date = EXPIRY) -> SliceFit:
    """fit_slice with the common AAPL/ACT-365 arguments, to keep the tests readable."""
    return fit_slice("AAPL", maturity, points, expiry_date=expiry, day_count="ACT/365")


def _params(fit: SliceFit) -> SurfaceParameters:
    return surface_parameters(fit, snapshot_ts=TS, source_snapshot_ts=TS, calc_ts=TS,
                              config_hash="cfg-hash-0")


def _grid(fit: SliceFit, buckets: tuple[float, ...]) -> tuple[SurfaceGrid, ...]:
    return surface_grid_cells(fit, buckets, snapshot_ts=TS, source_snapshot_ts=TS, calc_ts=TS,
                              config_hash="c")


def _svi_slice(params: SviParams, *, maturity: float) -> SliceFit:
    """A minimal SVI ``SliceFit`` carrying only the calibrated curve.

    Built directly (not via ``fit_slice``) so a cross-maturity test can place a known
    smile at a chosen maturity; the diagnostic fields are irrelevant to the curve.
    """
    return SliceFit(
        underlying="X", maturity_years=maturity, expiry_date=EXPIRY, day_count="ACT/365",
        method=METHOD_SVI, svi=params, rmse=0.0, n_points=MIN_POINTS_FOR_SVI, arb_free=True,
        bound_hits=(), butterfly_violations=(), nonparametric_ks=(), nonparametric_ws=(),
        raw_points=(),
    )


def _maturity_sweep(maturities: list[float]) -> tuple[float, ...]:
    """A sorted maturity sweep: below the first knot, every knot and between-knot
    midpoint, and above the last — so monotonicity is probed between knots, not only
    at them."""
    sweep = [maturities[0] * 0.5]
    for earlier, later in zip(maturities, maturities[1:], strict=False):
        sweep.append(earlier)
        sweep.append(0.5 * (earlier + later))
    sweep.append(maturities[-1])
    sweep.append(maturities[-1] * 1.5)
    return tuple(sweep)


# --------------------------------------------------------------------------- #
# SVI math (unit level)                                                       #
# --------------------------------------------------------------------------- #
def test_svi_total_variance_at_vertex_by_hand() -> None:
    # With m = 0, k = 0: w(0) = a + b*(rho*0 + sqrt(0 + sigma^2)) = a + b*sigma.
    params = SviParams(a=0.04, b=0.10, rho=-0.30, m=0.0, sigma=0.20)
    assert params.total_variance(0.0) == pytest.approx(0.04 + 0.10 * 0.20)
    # Cross-check the whole curve against the independent generator function.
    for k in (-0.3, -0.1, 0.0, 0.15, 0.3):
        assert params.total_variance(k) == pytest.approx(
            svi_total_variance(k, 0.04, 0.10, -0.30, 0.0, 0.20), rel=1e-12
        )


def test_svi_derivatives_match_finite_difference() -> None:
    params = SviParams(a=0.04, b=0.10, rho=-0.30, m=0.05, sigma=0.20)
    k, h = 0.12, 1e-6
    fd1 = (params.total_variance(k + h) - params.total_variance(k - h)) / (2 * h)
    fd2 = (
        params.total_variance(k + h) - 2 * params.total_variance(k) + params.total_variance(k - h)
    ) / (h * h)
    assert params.first_derivative(k) == pytest.approx(fd1, rel=1e-5)
    assert params.second_derivative(k) == pytest.approx(fd2, rel=1e-3)
    assert params.second_derivative(k) > 0.0  # convex in k whenever b > 0


# --------------------------------------------------------------------------- #
# SVI calibration — the known-answer oracle                                   #
# --------------------------------------------------------------------------- #
def test_fit_recovers_known_svi_parameters() -> None:
    ks = tuple(p.log_moneyness for p in _SURFACE.points)
    ws = tuple(p.total_variance for p in _SURFACE.points)
    fit = fit_svi(ks, ws)
    assert fit.converged
    recovered = (fit.params.a, fit.params.b, fit.params.rho, fit.params.m, fit.params.sigma)
    for got, true in zip(recovered, _TRUE, strict=True):
        assert got == pytest.approx(true, abs=1e-5)
    assert fit.rmse < 1e-9
    assert fit.bound_hits == ()


def test_fit_svi_needs_five_points() -> None:
    with pytest.raises(ValueError, match="at least 5"):
        fit_svi((0.0, 0.1, 0.2), (0.04, 0.05, 0.06))


def test_bound_hit_flags_are_set_when_a_parameter_pins() -> None:
    ks = (-0.25, -0.1, 0.0, 0.1, 0.25)
    # Generate from b at its upper bound (10); the fit must pin b there and flag it.
    upper = SviParams(a=0.04, b=10.0, rho=-0.3, m=0.0, sigma=0.2)
    assert "b_upper" in fit_svi(ks, tuple(upper.total_variance(k) for k in ks)).bound_hits
    # Generate from rho at its lower bound (-0.999); the fit must flag rho_lower.
    lower = SviParams(a=0.04, b=0.1, rho=-0.999, m=0.0, sigma=0.2)
    assert "rho_lower" in fit_svi(ks, tuple(lower.total_variance(k) for k in ks)).bound_hits


# --------------------------------------------------------------------------- #
# No-arbitrage diagnostics                                                    #
# --------------------------------------------------------------------------- #
def test_butterfly_passes_a_clean_smile_and_flags_a_bad_one() -> None:
    good = SviParams(*_TRUE)
    grid = tuple(-0.3 + 0.03 * i for i in range(21))
    assert butterfly_violations(good, grid) == ()
    assert butterfly_g(good, 0.0) > 0.0
    # A steep, highly-skewed slice violates the butterfly condition (g(k) < 0).
    bad = SviParams(a=0.01, b=2.0, rho=-0.95, m=0.0, sigma=0.05)
    assert len(butterfly_violations(bad, tuple(-1.0 + 0.1 * i for i in range(21)))) > 0


def test_butterfly_flags_nonpositive_total_variance() -> None:
    # A negative-variance slice is itself an arbitrage; flagged at that k.
    negative = SviParams(a=-1.0, b=0.01, rho=0.0, m=0.0, sigma=0.01)
    assert len(butterfly_violations(negative, (0.0,))) == 1


def test_calendar_monotonicity_flags_a_decreasing_slice() -> None:
    # Eq 21: total variance must not fall as maturity rises. Build a long maturity
    # whose variance dips below the short one at every k -> flagged.
    short = (0.25, lambda k: 0.10)
    long_bad = (0.50, lambda k: 0.05)
    grid = (-0.2, 0.0, 0.2)
    violations = calendar_violations([short, long_bad], grid)
    assert len(violations) == len(grid)
    assert all(v.maturity_short == 0.25 and v.maturity_long == 0.50 for v in violations)
    # The same maturities with increasing variance are clean.
    long_good = (0.50, lambda k: 0.20)
    assert calendar_violations([short, long_good], grid) == ()


@given(
    a=st.floats(min_value=0.0, max_value=0.5),
    b=st.floats(min_value=0.0, max_value=1.0),
    rho=st.floats(min_value=-0.999, max_value=0.999),
    m=st.floats(min_value=-0.5, max_value=0.5),
    sigma=st.floats(min_value=0.05, max_value=1.0),
    maturities=st.lists(
        st.floats(min_value=0.05, max_value=3.0), min_size=2, max_size=4, unique=True
    ).map(sorted),
)
@settings(max_examples=200)
def test_total_variance_is_non_decreasing_in_maturity(
    a: float, b: float, rho: float, m: float, sigma: float, maturities: list[float]
) -> None:
    # Eq 21 (calendar no-arbitrage) as a property over a range, not one hand fixture:
    # at fixed log-moneyness, total variance must not fall as maturity rises. Oracle:
    # a flat forward-variance term structure has w(k, T) = base(k) * T with base(k) >= 0
    # (guaranteed by a >= 0, b >= 0, sigma > 0), so w is non-decreasing in T at every k
    # by construction — independent of the surface code. That scaling is itself SVI with
    # (a, b) -> (a*T, b*T), so each slice is a genuine SVI smile.
    scaled = [SviParams(a=a * t, b=b * t, rho=rho, m=m, sigma=sigma) for t in maturities]
    # 1. The detector never flags a calendar-arb-free surface (no false positives).
    detector_input = [(t, p.total_variance) for t, p in zip(maturities, scaled, strict=True)]
    assert calendar_violations(detector_input, _CALENDAR_K_GRID) == ()
    # 2. The surface the engine *produces* is monotone in maturity between the knots
    #    too: read interpolate_total_variance across a dense sweep and assert it never
    #    dips at any k (a bad interpolation weight or bracket would surface here).
    slices = [_svi_slice(p, maturity=t) for t, p in zip(maturities, scaled, strict=True)]
    for k in _CALENDAR_K_GRID:
        ws = [interpolate_total_variance(slices, k, t) for t in _maturity_sweep(maturities)]
        for earlier, later in zip(ws, ws[1:], strict=False):
            assert later >= earlier - 1e-9 * (1.0 + abs(earlier))


# --------------------------------------------------------------------------- #
# Slice fitting and the nonparametric fallback                                 #
# --------------------------------------------------------------------------- #
def test_fit_slice_calibrates_svi_and_reproduces_points() -> None:
    points = _synthetic_points()
    fit = _fit(points)
    assert fit.method == METHOD_SVI
    assert fit.arb_free
    assert fit.svi is not None
    # Recovers the parameters (the generator is the oracle).
    recovered = (fit.svi.a, fit.svi.b, fit.svi.rho, fit.svi.m, fit.svi.sigma)
    for got, true in zip(recovered, _TRUE, strict=True):
        assert got == pytest.approx(true, abs=1e-5)
    # Reproduces every accepted point within the documented tolerance.
    for p in _SURFACE.points:
        assert fit.total_variance(p.log_moneyness) == pytest.approx(p.total_variance, abs=1e-6)
    # The raw points are retained after the fit, still queryable.
    assert len(fit.raw_points) == len(points)
    assert {pt.contract_key for pt in fit.raw_points} == {f"K{p.strike:g}" for p in _SURFACE.points}


def test_sparse_slice_falls_back_to_labeled_nonparametric() -> None:
    # Three points cannot identify five SVI parameters, so the slice is interpolated
    # and labeled nonparametric (never dressed up as a calibrated model).
    points = _synthetic_points()[:3]
    fit = _fit(points)
    assert fit.method == METHOD_NONPARAMETRIC
    assert fit.svi is None
    # The nonparametric curve passes through its own knots and interpolates between.
    for p in _SURFACE.points[:3]:
        assert fit.total_variance(p.log_moneyness) == pytest.approx(p.total_variance, abs=1e-12)
    mid_k = 0.5 * (points[0].k + points[1].k)
    between = fit.total_variance(mid_k)
    lo, hi = sorted((points[0].total_variance, points[1].total_variance))
    assert lo <= between <= hi
    # A point in the *second* interval (between knots 1 and 2) exercises the walk
    # past the first bracket, and still lands between its two neighbours.
    upper_mid_k = 0.5 * (points[1].k + points[2].k)
    upper_between = fit.total_variance(upper_mid_k)
    lo2, hi2 = sorted((points[1].total_variance, points[2].total_variance))
    assert lo2 <= upper_between <= hi2


def test_single_point_slice_is_flat_nonparametric() -> None:
    fit = _fit((_iv_point(0.0, 0.06, "K100"),), maturity=0.25)
    assert fit.method == METHOD_NONPARAMETRIC
    assert fit.total_variance(-0.5) == pytest.approx(0.06)  # flat extrapolation
    assert fit.total_variance(0.5) == pytest.approx(0.06)


def test_empty_slice_is_insufficient_and_has_no_curve() -> None:
    fit = _fit((), maturity=0.25)
    assert fit.method == METHOD_INSUFFICIENT
    with pytest.raises(ValueError, match="no curve"):
        fit.total_variance(0.0)


def test_duplicate_strikes_are_deduplicated() -> None:
    points = (*_synthetic_points(), _iv_point(0.0, 999.0, "K100-dup"))  # duplicate k=0
    fit = _fit(points)
    # The duplicate at k=0 did not corrupt the fit (first-seen wins, 999 ignored).
    assert fit.total_variance(0.0) == pytest.approx(0.06, abs=1e-4)


# --------------------------------------------------------------------------- #
# Cross-maturity interpolation (Eq 22)                                         #
# --------------------------------------------------------------------------- #
def test_interpolation_is_linear_in_total_variance_across_maturity() -> None:
    near = _fit(_synthetic_points(), maturity=0.25)
    far_points = tuple(_iv_point(p.log_moneyness, 2.0 * p.total_variance, f"F{p.strike:g}")
                       for p in _SURFACE.points)
    far = _fit(far_points, maturity=0.50, expiry=date(2026, 9, 18))
    slices = [near, far]
    # At the knots, interpolation returns each slice exactly.
    assert interpolate_total_variance(slices, 0.0, 0.25) == pytest.approx(0.06, abs=1e-5)
    assert interpolate_total_variance(slices, 0.0, 0.50) == pytest.approx(0.12, abs=1e-5)
    # Midway in maturity is midway in total variance (linear in w): 0.09 by hand.
    assert interpolate_total_variance(slices, 0.0, 0.375) == pytest.approx(0.09, abs=1e-5)
    # Outside the maturity range holds the nearest slice flat.
    assert interpolate_total_variance(slices, 0.0, 0.1) == pytest.approx(0.06, abs=1e-5)
    assert interpolate_total_variance(slices, 0.0, 1.0) == pytest.approx(0.12, abs=1e-5)
    # With a third slice, a maturity in the second interval must skip the first
    # bracket: w(0,1.0)=0.24, so midway between 0.50 and 1.0 (at 0.75) is 0.18.
    third_points = tuple(_iv_point(p.log_moneyness, 4.0 * p.total_variance, f"T{p.strike:g}")
                         for p in _SURFACE.points)
    third = _fit(third_points, maturity=1.0, expiry=date(2026, 12, 18))
    mid_high = interpolate_total_variance([near, far, third], 0.0, 0.75)
    assert mid_high == pytest.approx(0.18, abs=1e-5)


def test_interpolation_without_a_usable_slice_raises() -> None:
    empty = _fit((), maturity=0.25)
    with pytest.raises(ValueError, match="no slice"):
        interpolate_total_variance([empty], 0.0, 0.25)


# --------------------------------------------------------------------------- #
# Plotting utility                                                            #
# --------------------------------------------------------------------------- #
def test_plot_series_shows_fitted_curve_near_raw_points() -> None:
    fit = _fit(_synthetic_points())
    series = slice_plot_series(fit, n_grid=40)
    assert len(series.raw_k) == 5
    assert len(series.grid_k) == 40 == len(series.fitted_w)
    # The fitted curve evaluated at each raw k reproduces the raw total variance.
    for raw_k, raw_w in zip(series.raw_k, series.raw_w, strict=True):
        assert fit.total_variance(raw_k) == pytest.approx(raw_w, abs=1e-6)


def test_plot_series_refuses_an_insufficient_slice() -> None:
    empty = _fit((), maturity=0.25)
    with pytest.raises(ValueError, match="nothing to plot"):
        slice_plot_series(empty)


# --------------------------------------------------------------------------- #
# Contract adapters                                                           #
# --------------------------------------------------------------------------- #
def test_surface_parameters_is_a_valid_stamped_contract() -> None:
    fit = _fit(_synthetic_points())
    params = _params(fit)
    assert isinstance(params, SurfaceParameters)
    validate(params)  # raises if any contract field rule is violated (incl. svi_b>0, svi_sigma>0)
    assert table_for_contract(SurfaceParameters) == "surface_parameters"
    assert params.svi_a == pytest.approx(0.04, abs=1e-5)
    assert params.diagnostics.n_points == 5
    assert params.diagnostics.arb_free is True
    assert len(params.provenance.source_records) == 5  # one per feeding IvPoint


def test_surface_parameters_refuses_a_nonparametric_slice() -> None:
    sparse = _fit(_synthetic_points()[:3], maturity=0.25)
    with pytest.raises(ValueError, match="nonparametric"):
        _params(sparse)


def test_surface_grid_cells_are_valid_and_clamped_nonnegative() -> None:
    fit = _fit(_synthetic_points())
    buckets = (-0.2, -0.1, 0.0, 0.1, 0.2)
    cells = _grid(fit, buckets)
    assert len(cells) == len(buckets)
    for cell in cells:
        assert isinstance(cell, SurfaceGrid)
        validate(cell)  # total_variance must be non-negative
        assert cell.total_variance >= 0.0
    assert table_for_contract(SurfaceGrid) == "surface_grid"
    # The k=0 bucket reproduces the vertex total variance a + b*sigma = 0.06.
    assert cells[2].total_variance == pytest.approx(0.06, abs=1e-5)


def test_surface_grid_refuses_an_insufficient_slice() -> None:
    empty = _fit((), maturity=0.25)
    with pytest.raises(ValueError, match="insufficient"):
        _grid(empty, (0.0,))
