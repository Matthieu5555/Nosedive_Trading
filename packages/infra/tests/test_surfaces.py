from __future__ import annotations

import math
from datetime import UTC, date, datetime

import pytest
from algotrading.core.config import SurfaceConfig
from algotrading.core.config.platform_config import ConfigFieldError
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
_SURFACE = build_synthetic_surface()
_TRUE = (_SURFACE.svi_a, _SURFACE.svi_b, _SURFACE.svi_rho, _SURFACE.svi_m, _SURFACE.svi_sigma)
_CALENDAR_K_GRID = (-0.4, -0.2, 0.0, 0.2, 0.4)


def _iv_point(k: float, w: float, key: str) -> IvPoint:
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
    return SliceFit(
        underlying="X", maturity_years=maturity, expiry_date=EXPIRY, day_count="ACT/365",
        method=METHOD_SVI, svi=params, rmse=0.0, n_points=MIN_POINTS_FOR_SVI, arb_free=True,
        bound_hits=(), butterfly_violations=(), nonparametric_ks=(), nonparametric_ws=(),
        raw_points=(),
    )


def _maturity_sweep(maturities: list[float]) -> tuple[float, ...]:
    sweep = [maturities[0] * 0.5]
    for earlier, later in zip(maturities, maturities[1:], strict=False):
        sweep.append(earlier)
        sweep.append(0.5 * (earlier + later))
    sweep.append(maturities[-1])
    sweep.append(maturities[-1] * 1.5)
    return tuple(sweep)


def test_svi_total_variance_at_vertex_by_hand() -> None:
    params = SviParams(a=0.04, b=0.10, rho=-0.30, m=0.0, sigma=0.20)
    assert params.total_variance(0.0) == pytest.approx(0.04 + 0.10 * 0.20)
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
    assert params.second_derivative(k) > 0.0


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


def test_min_points_per_slice_config_drives_the_svi_vs_fallback_routing() -> None:
    points = _synthetic_points()
    n_distinct = len({p.log_moneyness for p in points})
    assert n_distinct >= MIN_POINTS_FOR_SVI
    assert _fit(points).method == METHOD_SVI

    demanding = SurfaceConfig.model_validate(
        {**SURFACE_CONFIG.model_dump(), "min_points_per_slice": n_distinct + 1}
    )
    routed = fit_slice(
        "AAPL", _SURFACE.maturity_years, points, expiry_date=EXPIRY, day_count="ACT/365",
        config=demanding,
    )
    assert routed.method == METHOD_NONPARAMETRIC


def test_min_points_per_slice_is_floored_at_the_svi_parameter_count() -> None:
    with pytest.raises(ConfigFieldError, match="greater than or equal to 5"):
        SurfaceConfig.model_validate({**SURFACE_CONFIG.model_dump(), "min_points_per_slice": 4})


def test_bound_hit_flags_are_set_when_a_parameter_pins() -> None:
    ks = (-0.25, -0.1, 0.0, 0.1, 0.25)
    upper = SviParams(a=0.04, b=10.0, rho=-0.3, m=0.0, sigma=0.2)
    assert "b_upper" in fit_svi(
        ks, tuple(upper.total_variance(k) for k in ks), config=SURFACE_CONFIG
    ).bound_hits
    lower = SviParams(a=0.04, b=0.1, rho=-0.999, m=0.0, sigma=0.2)
    assert "rho_lower" in fit_svi(
        ks, tuple(lower.total_variance(k) for k in ks), config=SURFACE_CONFIG
    ).bound_hits


def test_butterfly_passes_a_clean_smile_and_flags_a_bad_one() -> None:
    good = SviParams(*_TRUE)
    grid = tuple(-0.3 + 0.03 * i for i in range(21))
    assert butterfly_violations(good, grid) == ()
    assert butterfly_g(good, 0.0) > 0.0
    bad = SviParams(a=0.01, b=2.0, rho=-0.95, m=0.0, sigma=0.05)
    assert len(butterfly_violations(bad, tuple(-1.0 + 0.1 * i for i in range(21)))) > 0


def test_butterfly_flags_nonpositive_total_variance() -> None:
    negative = SviParams(a=-1.0, b=0.01, rho=0.0, m=0.0, sigma=0.01)
    assert len(butterfly_violations(negative, (0.0,))) == 1


def test_calendar_monotonicity_flags_a_decreasing_slice() -> None:
    short = (0.25, lambda k: 0.10)
    long_bad = (0.50, lambda k: 0.05)
    grid = (-0.2, 0.0, 0.2)
    violations = calendar_violations([short, long_bad], grid)
    assert len(violations) == len(grid)
    assert all(v.maturity_short == 0.25 and v.maturity_long == 0.50 for v in violations)
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
    scaled = [SviParams(a=a * t, b=b * t, rho=rho, m=m, sigma=sigma) for t in maturities]
    detector_input = [(t, p.total_variance) for t, p in zip(maturities, scaled, strict=True)]
    assert calendar_violations(detector_input, _CALENDAR_K_GRID) == ()
    slices = [_svi_slice(p, maturity=t) for t, p in zip(maturities, scaled, strict=True)]
    for k in _CALENDAR_K_GRID:
        ws = [interpolate_total_variance(slices, k, t) for t in _maturity_sweep(maturities)]
        for earlier, later in zip(ws, ws[1:], strict=False):
            assert later >= earlier - 1e-9 * (1.0 + abs(earlier))


def test_fit_slice_calibrates_svi_and_reproduces_points() -> None:
    points = _synthetic_points()
    fit = _fit(points)
    assert fit.method == METHOD_SVI
    assert fit.arb_free
    assert fit.svi is not None
    recovered = (fit.svi.a, fit.svi.b, fit.svi.rho, fit.svi.m, fit.svi.sigma)
    for got, true in zip(recovered, _TRUE, strict=True):
        assert got == pytest.approx(true, abs=1e-5)
    for p in _SURFACE.points:
        assert fit.total_variance(p.log_moneyness) == pytest.approx(p.total_variance, abs=1e-6)
    assert len(fit.raw_points) == len(points)
    assert {pt.contract_key for pt in fit.raw_points} == {f"K{p.strike:g}" for p in _SURFACE.points}


def test_fit_slice_labels_are_read_from_config_not_hardwired() -> None:
    relabelled = SURFACE_CONFIG.model_copy(
        update={"model": "svi-tagged", "fallback_model": "interp-tagged"}
    )
    dense = fit_slice("AAPL", 0.5, _synthetic_points(), expiry_date=date(2026, 6, 19),
                      day_count="ACT/365", config=relabelled)
    sparse = fit_slice("AAPL", 0.5, _synthetic_points()[:3], expiry_date=date(2026, 6, 19),
                       day_count="ACT/365", config=relabelled)
    assert dense.method == "svi-tagged"
    assert sparse.method == "interp-tagged"


def test_sparse_slice_falls_back_to_labeled_nonparametric() -> None:
    points = _synthetic_points()[:3]
    fit = _fit(points)
    assert fit.method == METHOD_NONPARAMETRIC
    assert fit.svi is None
    for p in _SURFACE.points[:3]:
        assert fit.total_variance(p.log_moneyness) == pytest.approx(p.total_variance, abs=1e-12)
    mid_k = 0.5 * (points[0].log_moneyness + points[1].log_moneyness)
    between = fit.total_variance(mid_k)
    lo, hi = sorted((points[0].total_variance, points[1].total_variance))
    assert lo <= between <= hi
    upper_mid_k = 0.5 * (points[1].log_moneyness + points[2].log_moneyness)
    upper_between = fit.total_variance(upper_mid_k)
    lo2, hi2 = sorted((points[1].total_variance, points[2].total_variance))
    assert lo2 <= upper_between <= hi2


def test_single_point_slice_is_flat_nonparametric() -> None:
    fit = _fit((_iv_point(0.0, 0.06, "K100"),), maturity=0.25)
    assert fit.method == METHOD_NONPARAMETRIC
    assert fit.total_variance(-0.5) == pytest.approx(0.06)
    assert fit.total_variance(0.5) == pytest.approx(0.06)


def test_empty_slice_is_insufficient_and_has_no_curve() -> None:
    fit = _fit((), maturity=0.25)
    assert fit.method == METHOD_INSUFFICIENT
    with pytest.raises(ValueError, match="no curve"):
        fit.total_variance(0.0)


def test_duplicate_strikes_are_deduplicated() -> None:
    points = (*_synthetic_points(), _iv_point(0.0, 999.0, "K100-dup"))
    fit = _fit(points)
    assert fit.total_variance(0.0) == pytest.approx(0.06, abs=1e-4)


def _flat_clamped_linear_interp(
    ks: tuple[float, ...], ws: tuple[float, ...], k: float
) -> float:
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
    from algotrading.infra.surfaces.fit import _interpolate_sorted

    assert _interpolate_sorted(ks, ws, ks[0] - 1.0) == ws[0]
    assert _interpolate_sorted(ks, ws, ks[-1] + 1.0) == ws[-1]
    assert _interpolate_sorted(ks, ws, ks[0]) == ws[0]
    assert _interpolate_sorted(ks, ws, ks[-1]) == ws[-1]
    for knot, value in zip(ks[1:-1], ws[1:-1], strict=True):
        assert _interpolate_sorted(ks, ws, knot) == value
    for left in range(len(ks) - 1):
        mid = 0.5 * (ks[left] + ks[left + 1])
        assert _interpolate_sorted(ks, ws, mid) == pytest.approx(
            _flat_clamped_linear_interp(ks, ws, mid), rel=1e-15, abs=1e-18
        )


def test_interpolate_sorted_clamp_edges_are_bit_identical_to_hand_rolled() -> None:
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
        ("irrational_ish", (-1.1, -0.7, -0.1, 0.3, 0.9), (0.123, 0.071, 0.0531, 0.0617, 0.143)),
        ("tiny_variances", (-0.05, 0.0, 0.05), (1.7e-7, 1.1e-7, 2.3e-7)),
    ],
)
def test_interpolate_sorted_is_bit_identical_to_the_numpy_form_on_a_dense_sweep(
    name: str, ks: tuple[float, ...], ws: tuple[float, ...]
) -> None:
    from algotrading.infra.surfaces.fit import _interpolate_sorted

    low, high = ks[0] - 0.2, ks[-1] + 0.2
    n = 10_001
    step = (high - low) / (n - 1)
    sweep = [low + i * step for i in range(n)]
    sweep.extend(ks)
    for k in sweep:
        new = _interpolate_sorted(ks, ws, k)
        old = _numpy_searchsorted_interpolate(ks, ws, k)
        assert new.hex() == old.hex(), (name, k)


def test_interpolate_sorted_rejects_a_nan_query() -> None:
    from algotrading.infra.surfaces.fit import _interpolate_sorted

    with pytest.raises(ValueError, match="NaN"):
        _interpolate_sorted((-0.5, 0.5), (0.04, 0.09), float("nan"))


def test_interpolation_is_linear_in_total_variance_across_maturity() -> None:
    near = _fit(_synthetic_points(), maturity=0.25)
    far_points = tuple(_iv_point(p.log_moneyness, 2.0 * p.total_variance, f"F{p.strike:g}")
                       for p in _SURFACE.points)
    far = _fit(far_points, maturity=0.50, expiry=date(2026, 9, 18))
    slices = [near, far]
    assert interpolate_total_variance(slices, 0.0, 0.25) == pytest.approx(0.06, abs=1e-5)
    assert interpolate_total_variance(slices, 0.0, 0.50) == pytest.approx(0.12, abs=1e-5)
    assert interpolate_total_variance(slices, 0.0, 0.375) == pytest.approx(0.09, abs=1e-5)
    assert interpolate_total_variance(slices, 0.0, 0.1) == pytest.approx(0.06, abs=1e-5)
    assert interpolate_total_variance(slices, 0.0, 1.0) == pytest.approx(0.12, abs=1e-5)
    third_points = tuple(_iv_point(p.log_moneyness, 4.0 * p.total_variance, f"T{p.strike:g}")
                         for p in _SURFACE.points)
    third = _fit(third_points, maturity=1.0, expiry=date(2026, 12, 18))
    mid_high = interpolate_total_variance([near, far, third], 0.0, 0.75)
    assert mid_high == pytest.approx(0.18, abs=1e-5)


def test_interpolation_without_a_usable_slice_raises() -> None:
    empty = _fit((), maturity=0.25)
    with pytest.raises(ValueError, match="no slice"):
        interpolate_total_variance([empty], 0.0, 0.25)


def test_plot_series_shows_fitted_curve_near_raw_points() -> None:
    fit = _fit(_synthetic_points())
    series = slice_plot_series(fit, n_grid=40)
    assert len(series.raw_k) == 5
    assert len(series.grid_k) == 40 == len(series.fitted_w)
    for raw_k, raw_w in zip(series.raw_k, series.raw_w, strict=True):
        assert fit.total_variance(raw_k) == pytest.approx(raw_w, abs=1e-6)


def test_plot_series_refuses_an_insufficient_slice() -> None:
    empty = _fit((), maturity=0.25)
    with pytest.raises(ValueError, match="nothing to plot"):
        slice_plot_series(empty)


def test_surface_parameters_is_a_valid_stamped_contract() -> None:
    fit = _fit(_synthetic_points())
    params = _params(fit)
    assert isinstance(params, SurfaceParameters)
    validate(params)
    assert table_for_contract(SurfaceParameters) == "surface_parameters"
    assert params.svi_a == pytest.approx(0.04, abs=1e-5)
    assert params.diagnostics.n_points == 5
    assert params.diagnostics.arb_free is True
    assert len(params.provenance.source_records) == 5


def test_surface_parameters_refuses_a_nonparametric_slice() -> None:
    sparse = _fit(_synthetic_points()[:3], maturity=0.25)
    with pytest.raises(ValueError, match="nonparametric"):
        _params(sparse)


def test_fit_slice_records_svi_convergence_and_nonparametric_records_none() -> None:
    assert _fit(_synthetic_points()).converged is True
    assert _fit(_synthetic_points()[:3]).converged is None
    assert _fit(()).converged is None


def test_surface_parameters_carry_bound_hits_and_converged() -> None:
    fit = _fit(_synthetic_points())
    diagnostics = _params(fit).diagnostics
    assert diagnostics.bound_hits == fit.bound_hits == ()
    assert diagnostics.converged is True

    ks = (-0.25, -0.1, 0.0, 0.1, 0.25)
    railed_params = SviParams(a=0.04, b=0.1, rho=-0.999, m=0.0, sigma=0.2)
    railed_points = tuple(
        _iv_point(k, railed_params.total_variance(k), f"R{i}") for i, k in enumerate(ks)
    )
    railed_diag = _params(_fit(railed_points)).diagnostics
    assert railed_diag.bound_hits is not None
    assert "rho_lower" in railed_diag.bound_hits


def test_degeneracy_reasons_flag_railed_and_arb_breached_fits_not_clean_ones() -> None:
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

    legacy = SurfaceFitDiagnostics(rmse=1e-6, n_points=9, arb_free=True)
    assert degeneracy_reasons(legacy) == ()


def test_legacy_surface_parameters_row_reads_back_with_null_degeneracy_fields() -> None:
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
        validate(cell)
        assert cell.total_variance >= 0.0
    assert table_for_contract(SurfaceGrid) == "surface_grid"
    assert cells[2].total_variance == pytest.approx(0.06, abs=1e-5)


def test_surface_grid_refuses_an_insufficient_slice() -> None:
    empty = _fit((), maturity=0.25)
    with pytest.raises(ValueError, match="insufficient"):
        _grid(empty, (0.0,))
