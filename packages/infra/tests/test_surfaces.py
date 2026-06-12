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
from algotrading.core.provenance import source_ref, stamp
from algotrading.infra.contracts import (
    IvDiagnostics,
    IvPoint,
    SurfaceGrid,
    SurfaceParameters,
    table_for_contract,
    validate,
)
from algotrading.infra.surfaces import (
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
from fixtures.library import SURFACE_CONFIG
from fixtures.synthetic import build_synthetic_surface, svi_total_variance
from hypothesis import given, settings
from hypothesis import strategies as st

TS = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)
EXPIRY = date(2026, 6, 19)
_SURFACE = build_synthetic_surface()  # F=100, DF=0.99, T=0.25
_TRUE = (_SURFACE.svi_a, _SURFACE.svi_b, _SURFACE.svi_rho, _SURFACE.svi_m, _SURFACE.svi_sigma)
_CALENDAR_K_GRID = (-0.4, -0.2, 0.0, 0.2, 0.4)  # log-moneyness probed by the calendar property


def _iv_point(k: float, w: float, key: str) -> IvPoint:
    """A minimal valid IvPoint carrying log-moneyness k and total variance w."""
    a_stamp = stamp(
        calc_ts=TS, code_version="iv-1", config_hashes={"cfg": "c"},
        source_records=(source_ref("market_state_snapshots", TS, key),), source_timestamps=(TS,),
    )
    iv = math.sqrt(w / _SURFACE.maturity_years) if w > 0 else 0.0
    return IvPoint(
        snapshot_ts=TS, contract_key=key, implied_vol=iv, log_moneyness=k, total_variance=w, solver_version="iv-1",
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
    return fit_slice(
        "AAPL", maturity, points, expiry_date=expiry, day_count="ACT/365", config=SURFACE_CONFIG
    )


def _params(fit: SliceFit) -> SurfaceParameters:
    return surface_parameters(fit, snapshot_ts=TS, source_snapshot_ts=TS, calc_ts=TS,
                              config_hashes={"cfg": "cfg-hash-0"})


def _grid(fit: SliceFit, buckets: tuple[float, ...]) -> tuple[SurfaceGrid, ...]:
    return surface_grid_cells(fit, buckets, snapshot_ts=TS, source_snapshot_ts=TS, calc_ts=TS,
                              config_hashes={"cfg": "c"})


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
    fit = fit_svi(ks, ws, config=SURFACE_CONFIG)
    assert fit.converged
    recovered = (fit.params.a, fit.params.b, fit.params.rho, fit.params.m, fit.params.sigma)
    for got, true in zip(recovered, _TRUE, strict=True):
        assert got == pytest.approx(true, abs=1e-5)
    assert fit.rmse < 1e-9
    assert fit.bound_hits == ()


def test_fit_svi_needs_five_points() -> None:
    with pytest.raises(ValueError, match="at least 5"):
        fit_svi((0.0, 0.1, 0.2), (0.04, 0.05, 0.06), config=SURFACE_CONFIG)


def test_bound_hit_flags_are_set_when_a_parameter_pins() -> None:
    ks = (-0.25, -0.1, 0.0, 0.1, 0.25)
    # Generate from b at its upper bound (10); the fit must pin b there and flag it.
    upper = SviParams(a=0.04, b=10.0, rho=-0.3, m=0.0, sigma=0.2)
    assert "b_upper" in fit_svi(
        ks, tuple(upper.total_variance(k) for k in ks), config=SURFACE_CONFIG
    ).bound_hits
    # Generate from rho at its lower bound (-0.999); the fit must flag rho_lower.
    lower = SviParams(a=0.04, b=0.1, rho=-0.999, m=0.0, sigma=0.2)
    assert "rho_lower" in fit_svi(
        ks, tuple(lower.total_variance(k) for k in ks), config=SURFACE_CONFIG
    ).bound_hits


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
    mid_k = 0.5 * (points[0].log_moneyness + points[1].log_moneyness)
    between = fit.total_variance(mid_k)
    lo, hi = sorted((points[0].total_variance, points[1].total_variance))
    assert lo <= between <= hi
    # A point in the *second* interval (between knots 1 and 2) exercises the walk
    # past the first bracket, and still lands between its two neighbours.
    upper_mid_k = 0.5 * (points[1].log_moneyness + points[2].log_moneyness)
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
# Nonparametric interpolation kernel (_interpolate_sorted; stdlib-bisect since   #
# M38, byte-identical to the numpy-backed REP1 form — pinned below)              #
# --------------------------------------------------------------------------- #
def _flat_clamped_linear_interp(
    ks: tuple[float, ...], ws: tuple[float, ...], k: float
) -> float:
    """Independent oracle for the kernel's contract: linear in w, flat past the ends.

    Derived from the documented behaviour (REP1 / the docstring), not from the code
    under test: clamp to the end value outside ``[ks[0], ks[-1]]``, otherwise find the
    bracketing pair by a plain scan and linearly interpolate. Written deliberately
    differently from the implementation so it cannot mirror an implementation bug.
    """
    if k <= ks[0]:
        return ws[0]
    if k >= ks[-1]:
        return ws[-1]
    for left in range(len(ks) - 1):
        if ks[left] <= k <= ks[left + 1]:
            frac = (k - ks[left]) / (ks[left + 1] - ks[left])
            return ws[left] * (1.0 - frac) + ws[left + 1] * frac
    raise AssertionError("k was bracketed by the guards above")  # pragma: no cover


@pytest.mark.parametrize(
    ("name", "ks", "ws"),
    [
        ("two_knots", (-0.5, 0.5), (0.04, 0.09)),
        ("three_knots", (-1.0, 0.0, 1.0), (0.10, 0.04, 0.12)),
        ("uneven_spacing", (-2.0, -0.3, 0.1, 1.7), (0.20, 0.07, 0.05, 0.15)),
    ],
)
def test_interpolate_sorted_matches_flat_clamped_linear_oracle(
    name: str, ks: tuple[float, ...], ws: tuple[float, ...]
) -> None:
    """The numpy-backed kernel reproduces the flat-clamped linear oracle.

    Covers the two clamp edges (strictly outside, and exactly *at* the end knots),
    every interior knot (must return its own w exactly), and interior midpoints.
    """
    from algotrading.infra.surfaces.fit import _interpolate_sorted

    # Edges: strictly beyond the ends clamp flat to the end value (bit-exact).
    assert _interpolate_sorted(ks, ws, ks[0] - 1.0) == ws[0]
    assert _interpolate_sorted(ks, ws, ks[-1] + 1.0) == ws[-1]
    # The end knots themselves also take the clamp path and return the end value exactly.
    assert _interpolate_sorted(ks, ws, ks[0]) == ws[0]
    assert _interpolate_sorted(ks, ws, ks[-1]) == ws[-1]
    # Every interior knot returns its own total variance exactly.
    for knot, value in zip(ks[1:-1], ws[1:-1], strict=True):
        assert _interpolate_sorted(ks, ws, knot) == value
    # Interior midpoints match the independent linear oracle to float tolerance.
    for left in range(len(ks) - 1):
        mid = 0.5 * (ks[left] + ks[left + 1])
        assert _interpolate_sorted(ks, ws, mid) == pytest.approx(
            _flat_clamped_linear_interp(ks, ws, mid), rel=1e-15, abs=1e-18
        )


def test_interpolate_sorted_clamp_edges_are_bit_identical_to_hand_rolled() -> None:
    """The clamp edges (and end knots) match the pre-REP1 hand-rolled loop bit-for-bit.

    The edges feed ``total_variance`` and thus the ``SurfaceGrid`` content hash, so they
    must not shift by even one ULP. The hand-rolled reference is reproduced here verbatim
    from the routine REP1 replaced; the interior is allowed to differ by op-ordering, the
    edges are not.
    """
    from algotrading.infra.surfaces.fit import _interpolate_sorted

    def hand_rolled(ks: tuple[float, ...], ws: tuple[float, ...], k: float) -> float:
        if k <= ks[0]:
            return ws[0]
        if k >= ks[-1]:
            return ws[-1]
        for index in range(1, len(ks)):
            if k <= ks[index]:
                span = ks[index] - ks[index - 1]
                weight = (k - ks[index - 1]) / span
                return ws[index - 1] + weight * (ws[index] - ws[index - 1])
        raise AssertionError  # pragma: no cover

    ks = (-2.0, -0.3, 0.1, 1.7)
    ws = (0.20, 0.07, 0.05, 0.15)
    for k in (ks[0] - 5.0, ks[0], ks[-1], ks[-1] + 5.0):
        assert _interpolate_sorted(ks, ws, k) == hand_rolled(ks, ws, k)


def _numpy_searchsorted_interpolate(
    ks: tuple[float, ...], ws: tuple[float, ...], k: float
) -> float:
    """The pre-M38 numpy-backed kernel, reproduced verbatim as the bit-identity oracle.

    This is the exact routine M38 replaced (``np.asarray`` + ``np.searchsorted`` per
    scalar call); the replacement delegates the bracket search to ``bisect.bisect_left``
    and keeps the interior arithmetic. The two must agree bit-for-bit on every input —
    the interpolant feeds ``total_variance`` and thus the ``SurfaceGrid`` content hash.
    """
    import numpy as np

    knots = np.asarray(ks)
    values = np.asarray(ws)
    if k <= knots[0]:
        return float(values[0])
    if k >= knots[-1]:
        return float(values[-1])
    index = int(np.searchsorted(knots, k, side="left"))
    span = knots[index] - knots[index - 1]
    weight = (k - knots[index - 1]) / span
    return float(values[index - 1] + weight * (values[index] - values[index - 1]))


@pytest.mark.parametrize(
    ("name", "ks", "ws"),
    [
        ("two_knots", (-0.5, 0.5), (0.04, 0.09)),
        ("uneven_spacing", (-2.0, -0.3, 0.1, 1.7), (0.20, 0.07, 0.05, 0.15)),
        # Awkward, non-representable floats so the sweep exercises real rounding.
        ("irrational_ish", (-1.1, -0.7, -0.1, 0.3, 0.9), (0.123, 0.071, 0.0531, 0.0617, 0.143)),
        ("tiny_variances", (-0.05, 0.0, 0.05), (1.7e-7, 1.1e-7, 2.3e-7)),
    ],
)
def test_interpolate_sorted_is_bit_identical_to_the_numpy_form_on_a_dense_sweep(
    name: str, ks: tuple[float, ...], ws: tuple[float, ...]
) -> None:
    """M38 hash gate: stdlib-bisect kernel == old numpy kernel, bit-for-bit, densely.

    Sweeps 10_001 evaluation points spanning past both ends (so the clamp branches,
    every knot, and the interior all get hit) and compares against the verbatim
    pre-M38 numpy implementation. ``float.hex()`` equality is deliberate: the gate is
    bit-identity, not closeness — a one-ULP shift would move persisted surface bytes.
    """
    from algotrading.infra.surfaces.fit import _interpolate_sorted

    low, high = ks[0] - 0.2, ks[-1] + 0.2
    n = 10_001
    step = (high - low) / (n - 1)
    sweep = [low + i * step for i in range(n)]
    sweep.extend(ks)  # every knot exactly
    for k in sweep:
        new = _interpolate_sorted(ks, ws, k)
        old = _numpy_searchsorted_interpolate(ks, ws, k)
        assert new.hex() == old.hex(), (name, k)


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


# --------------------------------------------------------------------------- #
# Degeneracy diagnostics (bound hits / convergence propagated, never dropped)  #
# --------------------------------------------------------------------------- #
def test_fit_slice_records_svi_convergence_and_nonparametric_records_none() -> None:
    # The SVI path carries the optimizer's verdict; the fallback paths have no optimizer,
    # so converged is None (unknown), never a fabricated True.
    assert _fit(_synthetic_points()).converged is True
    assert _fit(_synthetic_points()[:3]).converged is None
    assert _fit(()).converged is None


def test_surface_parameters_carry_bound_hits_and_converged() -> None:
    # The persisted diagnostics must carry the degeneracy facts the fit computed —
    # a slice railed to its bound was previously served as if clean (audit, untracked).
    fit = _fit(_synthetic_points())
    diagnostics = _params(fit).diagnostics
    assert diagnostics.bound_hits == fit.bound_hits == ()
    assert diagnostics.converged is True

    # A smile generated from rho pinned at the feasible edge (-0.999, the live SX5E/SPX
    # shape) must flag rho_lower in the persisted diagnostics.
    ks = (-0.25, -0.1, 0.0, 0.1, 0.25)
    railed_params = SviParams(a=0.04, b=0.1, rho=-0.999, m=0.0, sigma=0.2)
    railed_points = tuple(
        _iv_point(k, railed_params.total_variance(k), f"R{i}") for i, k in enumerate(ks)
    )
    railed_diag = _params(_fit(railed_points)).diagnostics
    assert railed_diag.bound_hits is not None
    assert "rho_lower" in railed_diag.bound_hits


def test_degeneracy_reasons_flag_railed_and_arb_breached_fits_not_clean_ones() -> None:
    # The one policy home (T-vol-surface-correctness: flag, don't silently serve).
    from algotrading.infra.contracts import SurfaceFitDiagnostics
    from algotrading.infra.surfaces import degeneracy_reasons

    clean = SurfaceFitDiagnostics(
        rmse=1e-6, n_points=9, arb_free=True, bound_hits=(), converged=True
    )
    assert degeneracy_reasons(clean) == ()

    railed = SurfaceFitDiagnostics(
        rmse=1e-6, n_points=5, arb_free=False, bound_hits=("rho_lower",), converged=False
    )
    assert degeneracy_reasons(railed) == (
        "param_at_bound:rho_lower", "not_converged", "butterfly_arbitrage",
    )

    # Old persisted rows carry None for the additive fields: unknown is not degenerate.
    legacy = SurfaceFitDiagnostics(rmse=1e-6, n_points=9, arb_free=True)
    assert degeneracy_reasons(legacy) == ()


def test_legacy_surface_parameters_row_reads_back_with_null_degeneracy_fields() -> None:
    # Back-compat at the storage seam: a row persisted before bound_hits/converged were
    # added has no such keys in its diagnostics JSON; the additive-nullable rule must
    # read it back as None, not raise SchemaCompatibilityError.
    import json as _json

    from algotrading.infra.storage.serialization import from_row, to_row

    row = to_row(SurfaceParameters, _params(_fit(_synthetic_points())))
    diagnostics = _json.loads(row["diagnostics"])
    del diagnostics["bound_hits"]
    del diagnostics["converged"]
    row["diagnostics"] = _json.dumps(diagnostics)
    back = from_row(SurfaceParameters, row)
    assert isinstance(back, SurfaceParameters)
    assert back.diagnostics.bound_hits is None
    assert back.diagnostics.converged is None
    assert back.diagnostics.rmse == pytest.approx(0.0, abs=1e-9)


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
