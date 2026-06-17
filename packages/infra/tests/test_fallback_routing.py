"""Lane 3 (ADR 0056) — route a railed, dense SVI slice to the nonparametric fallback.

A GENUINELY railed SVI on a DENSE slice serves the smooth nonparametric fallback when the opt-in
``reroute_railed_dense_slice`` knob is ON, and stays served-as-railed-SVI (byte-identical default)
when OFF. Flag-not-reject is preserved: every SVI diagnostic still flags the slice. Lane-1 behaviour
(benign ``a_lower`` sink, thin slices) is NOT newly rerouted.

The railed slice is built by sampling a TRUE rho=-0.999 SVI, so the least-squares optimum sits ON
the rho bound; the expected fallback values are then the linear interpolation of those total
variances, derived independently of the production interpolator.
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest
from algotrading.core.config import SurfaceConfig
from algotrading.core.provenance import source_ref, stamp
from algotrading.infra.contracts import IvDiagnostics, IvPoint
from algotrading.infra.surfaces import (
    METHOD_NONPARAMETRIC,
    METHOD_SVI,
    SliceFit,
    SviParams,
    fit_slice,
    genuine_degeneracy_reasons,
)

TS = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)
EXPIRY = date(2026, 9, 18)
MATURITY = 0.25

# A true SVI with rho pinned to the -0.999 bound: sampling it and re-fitting forces the
# least-squares optimum onto rho_lower (an economically-meaningful rail, NOT the benign a_lower).
_RAILED_TRUE = SviParams(a=0.012, b=0.20, rho=-0.999, m=0.0, sigma=0.05)
# A clean, well-inside SVI (no rail) for the negative controls.
_CLEAN_TRUE = SviParams(a=0.020, b=0.10, rho=-0.30, m=0.0, sigma=0.10)

_DENSE_KS = (-0.30, -0.20, -0.12, -0.05, 0.0, 0.06, 0.14, 0.30)


def _base_config(**overrides: object) -> SurfaceConfig:
    return SurfaceConfig(
        version="reroute-test",
        svi_a_bounds=(0.0, 10.0),
        svi_b_bounds=(1e-8, 10.0),
        svi_rho_bounds=(-0.999, 0.999),
        svi_m_bounds=(-5.0, 5.0),
        svi_sigma_bounds=(1e-8, 10.0),
        svi_bound_hit_tol=1e-5,
        svi_max_iterations=200,
        **overrides,
    )


def _iv_point(k: float, w: float, key: str) -> IvPoint:
    a_stamp = stamp(
        calc_ts=TS,
        code_version="iv-1",
        config_hashes={"cfg": "c"},
        source_records=(source_ref("market_state_snapshots", TS, key),),
        source_timestamps=(TS,),
    )
    iv = math.sqrt(w / MATURITY) if w > 0 else 0.0
    return IvPoint(
        snapshot_ts=TS,
        contract_key=key,
        implied_vol=iv,
        log_moneyness=k,
        total_variance=w,
        solver_version="iv-1",
        diagnostics=IvDiagnostics(converged=True, iterations=5, residual=1e-12, status="converged"),
        source_snapshot_ts=TS,
        provenance=a_stamp,
    )


def _points_from(true: SviParams, ks: tuple[float, ...]) -> tuple[IvPoint, ...]:
    return tuple(_iv_point(k, true.total_variance(k), f"K{k:g}") for k in ks)


def _fit(points: tuple[IvPoint, ...], *, config: SurfaceConfig) -> SliceFit:
    return fit_slice(
        "X", MATURITY, points, expiry_date=EXPIRY, day_count="ACT/365", config=config
    )


def _expected_linear_interp(ks: tuple[float, ...], ws: tuple[float, ...], k: float) -> float:
    """Linear interp in total variance — independent of the production interpolator."""
    if k <= ks[0]:
        return ws[0]
    if k >= ks[-1]:
        return ws[-1]
    for i in range(1, len(ks)):
        if ks[i - 1] <= k <= ks[i]:
            weight = (k - ks[i - 1]) / (ks[i] - ks[i - 1])
            return ws[i - 1] + weight * (ws[i] - ws[i - 1])
    raise AssertionError("k not bracketed")  # pragma: no cover


# --- the railed dense slice rails as designed -----------------------------------------------------


def test_dense_slice_genuinely_rails_rho() -> None:
    """Sampling a true rho=-0.999 SVI re-fits onto the rho_lower bound (a genuine rail)."""
    fit = _fit(_points_from(_RAILED_TRUE, _DENSE_KS), config=_base_config())
    assert fit.method == METHOD_SVI
    assert fit.n_points >= _base_config().min_points_per_slice  # dense, not thin
    assert "rho_lower" in fit.bound_hits
    assert fit.rmse < 1e-6  # over-fit on a railed parameter, not a high-error fit
    assert genuine_degeneracy_reasons(fit)  # the rail is a genuine (non-benign) reason


# --- ON: reroute to the fallback, flags preserved -------------------------------------------------


def test_railed_dense_reroutes_to_fallback_when_on() -> None:
    points = _points_from(_RAILED_TRUE, _DENSE_KS)
    on = _fit(points, config=_base_config(reroute_railed_dense_slice=True))

    # served curve is the nonparametric fallback ...
    assert on.method == METHOD_NONPARAMETRIC
    # ... but flag-not-reject is preserved: every SVI diagnostic is carried through unchanged.
    assert on.svi is not None
    assert "rho_lower" in on.bound_hits
    assert genuine_degeneracy_reasons(on)  # still flags as degenerate (QC still FAILs it)


def test_rerouted_served_curve_matches_independent_linear_interp() -> None:
    points = _points_from(_RAILED_TRUE, _DENSE_KS)
    ws = tuple(_RAILED_TRUE.total_variance(k) for k in _DENSE_KS)
    on = _fit(points, config=_base_config(reroute_railed_dense_slice=True))

    for k in (-0.025, 0.03, -0.30, 0.30, -0.5, 0.5):
        expected = _expected_linear_interp(_DENSE_KS, ws, k)
        assert on.total_variance(k) == pytest.approx(expected, abs=1e-12)


def test_rerouted_curve_differs_from_railed_svi() -> None:
    """The whole point: the served curve actually changes when the knob flips."""
    points = _points_from(_RAILED_TRUE, _DENSE_KS)
    off = _fit(points, config=_base_config())
    on = _fit(points, config=_base_config(reroute_railed_dense_slice=True))
    # at an interior off-node point the railed SVI and the linear interp disagree materially
    assert off.total_variance(-0.025) != pytest.approx(on.total_variance(-0.025), abs=1e-6)


# --- OFF (default): byte-identical, serves the railed SVI -----------------------------------------


def test_default_off_serves_railed_svi_byte_identical() -> None:
    points = _points_from(_RAILED_TRUE, _DENSE_KS)
    default = _fit(points, config=_base_config())
    explicit_off = _fit(points, config=_base_config(reroute_railed_dense_slice=False))

    assert _base_config().reroute_railed_dense_slice is False  # shipped default is OFF
    assert default.method == METHOD_SVI
    # default == explicit-OFF, field-for-field (byte-identical default)
    assert default == explicit_off
    # serves the railed SVI curve, not the fallback
    assert default.total_variance(-0.025) == pytest.approx(
        default.svi.total_variance(-0.025) if default.svi else float("nan")
    )


# --- Lane-1 behaviour preserved: benign a_lower and thin slices are NOT newly rerouted ------------


def test_benign_a_floor_slice_not_rerouted() -> None:
    """A clean, well-inside fit (no genuine rail) is never rerouted even with the knob ON."""
    points = _points_from(_CLEAN_TRUE, _DENSE_KS)
    on = _fit(points, config=_base_config(reroute_railed_dense_slice=True))
    assert on.method == METHOD_SVI  # no genuine degeneracy → stays served-SVI
    assert not genuine_degeneracy_reasons(on)


def test_explicit_benign_a_lower_hit_not_rerouted() -> None:
    """An a_lower bound hit with a positive minimum total variance is benign (Lane 1) → not rerouted."""
    fit = _fit(_points_from(_CLEAN_TRUE, _DENSE_KS), config=_base_config(reroute_railed_dense_slice=True))
    assert fit.svi is not None
    benign = replace(
        fit,
        method=METHOD_SVI,
        bound_hits=("a_lower",),
        arb_free=True,
        svi=SviParams(a=0.0, b=fit.svi.b, rho=fit.svi.rho, m=fit.svi.m, sigma=fit.svi.sigma),
    )
    assert benign.svi.minimum_total_variance() > 0.0  # the benign condition holds
    assert genuine_degeneracy_reasons(benign) == ()  # exempted exactly as QC does


def test_thin_slice_uses_existing_sparse_fallback_not_the_reroute() -> None:
    """A genuinely thin slice falls back via the existing sparse path, untouched by Lane 3."""
    thin_ks = (-0.10, -0.02, 0.08)  # 3 < min_points_per_slice(5): never reaches the SVI path
    points = _points_from(_RAILED_TRUE, thin_ks)
    off = _fit(points, config=_base_config())
    on = _fit(points, config=_base_config(reroute_railed_dense_slice=True))
    # identical whether the reroute is on or off (it only governs the dense path)
    assert off.method == METHOD_NONPARAMETRIC
    assert on == off
    assert on.svi is None  # the thin fallback never fit SVI
    assert on.bound_hits == ()  # no rail to flag


# --- the dense floor knob -------------------------------------------------------------------------


def test_reroute_min_points_floor_gates_density() -> None:
    """Raising reroute_min_points above the slice's point count suppresses the reroute."""
    points = _points_from(_RAILED_TRUE, _DENSE_KS)  # 8 distinct points
    high_floor = _fit(
        points,
        config=_base_config(reroute_railed_dense_slice=True, reroute_min_points=9),
    )
    assert high_floor.method == METHOD_SVI  # 8 < 9 → not dense enough → stays railed-SVI
    at_floor = _fit(
        points,
        config=_base_config(reroute_railed_dense_slice=True, reroute_min_points=8),
    )
    assert at_floor.method == METHOD_NONPARAMETRIC  # 8 >= 8 → rerouted


def test_reroute_point_floor_defaults_to_min_points_per_slice() -> None:
    cfg = _base_config(reroute_min_points=None)
    assert cfg.reroute_point_floor == cfg.min_points_per_slice
    cfg2 = _base_config(reroute_min_points=7)
    assert cfg2.reroute_point_floor == 7
