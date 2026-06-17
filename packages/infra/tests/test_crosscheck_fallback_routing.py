"""INDEPENDENT cross-check of the railed-dense-slice reroute — commit e598f38, ADR 0056.

Adversarial second opinion on ``fit._should_reroute_railed_dense`` /
``_reroute_railed_to_fallback``. The implementer's ``test_fallback_routing.py`` rails
``rho`` to the LOWER bound (-0.999) on one fixed knot grid. This cross-check rails
``rho`` to the UPPER bound (+0.999) on a DIFFERENT, asymmetric knot grid, and
recomputes one fallback total-variance value with a from-scratch interpolation
written independently of both the production interpolator and the implementer's.

What is verified (different construction, same invariants ADR 0056 promises):
1. DEFAULT-OFF is byte-identical: a railed dense slice still serves SVI, and the
   default object equals the explicit-OFF object field-for-field.
2. ON reroutes to the nonparametric fallback while CARRYING every SVI diagnostic
   through unchanged (flag-not-reject).
3. The served fallback curve matches an independently hand-rolled linear interp.
4. A benign / clean slice and a thin slice are NOT newly rerouted.
"""

from __future__ import annotations

import math
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

_TS = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)
_EXPIRY = date(2026, 9, 18)
_MATURITY = 0.5

# Rail rho to the UPPER bound (+0.999) — the opposite rail from the implementer.
_RAILED_UP_TRUE = SviParams(a=0.015, b=0.18, rho=0.999, m=0.0, sigma=0.06)
# A clean, well-inside SVI for the negative control.
_CLEAN_TRUE = SviParams(a=0.030, b=0.09, rho=-0.20, m=0.0, sigma=0.12)

# An asymmetric, 9-point dense grid that does NOT match the implementer's grid.
_DENSE_KS = (-0.35, -0.22, -0.13, -0.07, -0.01, 0.04, 0.11, 0.19, 0.33)


def _base_config(**overrides: object) -> SurfaceConfig:
    return SurfaceConfig(
        version="xcheck-reroute",
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
        calc_ts=_TS,
        code_version="iv-1",
        config_hashes={"cfg": "c"},
        source_records=(source_ref("market_state_snapshots", _TS, key),),
        source_timestamps=(_TS,),
    )
    iv = math.sqrt(w / _MATURITY) if w > 0 else 0.0
    return IvPoint(
        snapshot_ts=_TS,
        contract_key=key,
        implied_vol=iv,
        log_moneyness=k,
        total_variance=w,
        solver_version="iv-1",
        diagnostics=IvDiagnostics(converged=True, iterations=5, residual=1e-12, status="converged"),
        source_snapshot_ts=_TS,
        provenance=a_stamp,
    )


def _points_from(true: SviParams, ks: tuple[float, ...]) -> tuple[IvPoint, ...]:
    return tuple(_iv_point(k, true.total_variance(k), f"K{k:g}") for k in ks)


def _fit(points: tuple[IvPoint, ...], *, config: SurfaceConfig) -> SliceFit:
    return fit_slice(
        "Y", _MATURITY, points, expiry_date=_EXPIRY, day_count="ACT/365", config=config
    )


def _independent_linear_interp(ks: tuple[float, ...], ws: tuple[float, ...], k: float) -> float:
    """Plain piecewise-linear interpolation in total variance, flat outside the hull.

    Hand-rolled here so it shares no code with the production ``_interpolate_sorted``
    nor the implementer's helper — a genuinely separate oracle.
    """
    pairs = sorted(zip(ks, ws, strict=True))
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    if k <= xs[0]:
        return ys[0]
    if k >= xs[-1]:
        return ys[-1]
    lo = 0
    while xs[lo + 1] < k:
        lo += 1
    t = (k - xs[lo]) / (xs[lo + 1] - xs[lo])
    return ys[lo] + t * (ys[lo + 1] - ys[lo])


def test_upper_railed_dense_slice_genuinely_rails() -> None:
    fit = _fit(_points_from(_RAILED_UP_TRUE, _DENSE_KS), config=_base_config())
    assert fit.method == METHOD_SVI
    assert fit.n_points >= _base_config().min_points_per_slice
    assert "rho_upper" in fit.bound_hits
    assert genuine_degeneracy_reasons(fit)  # the rail is a genuine reason


def test_default_off_is_byte_identical_and_serves_railed_svi() -> None:
    points = _points_from(_RAILED_UP_TRUE, _DENSE_KS)
    default = _fit(points, config=_base_config())
    explicit_off = _fit(points, config=_base_config(reroute_railed_dense_slice=False))

    assert _base_config().reroute_railed_dense_slice is False
    assert default.method == METHOD_SVI
    assert default == explicit_off  # field-for-field identical
    # Served curve is the railed SVI itself, not the fallback.
    assert default.svi is not None
    assert default.total_variance(0.02) == pytest.approx(default.svi.total_variance(0.02))


def test_on_reroutes_to_fallback_and_carries_every_svi_flag() -> None:
    points = _points_from(_RAILED_UP_TRUE, _DENSE_KS)
    on = _fit(points, config=_base_config(reroute_railed_dense_slice=True))

    assert on.method == METHOD_NONPARAMETRIC  # served curve switched
    # flag-not-reject: every diagnostic preserved for QC/audit.
    assert on.svi is not None
    assert "rho_upper" in on.bound_hits
    assert genuine_degeneracy_reasons(on)  # still fails QC on the genuine rail


def test_rerouted_curve_matches_independent_interp_oracle() -> None:
    points = _points_from(_RAILED_UP_TRUE, _DENSE_KS)
    ws = tuple(_RAILED_UP_TRUE.total_variance(k) for k in _DENSE_KS)
    on = _fit(points, config=_base_config(reroute_railed_dense_slice=True))

    # Independently recompute one interior fallback value, plus boundary/outside.
    for k in (-0.04, 0.07, -0.35, 0.33, -1.0, 1.0):
        expected = _independent_linear_interp(_DENSE_KS, ws, k)
        assert on.total_variance(k) == pytest.approx(expected, abs=1e-12)


def test_rerouted_value_differs_from_the_railed_svi() -> None:
    # The reroute must actually change the served value at an off-node interior point.
    points = _points_from(_RAILED_UP_TRUE, _DENSE_KS)
    off = _fit(points, config=_base_config())
    on = _fit(points, config=_base_config(reroute_railed_dense_slice=True))
    assert off.total_variance(-0.04) != pytest.approx(on.total_variance(-0.04), abs=1e-6)


def test_clean_dense_slice_is_not_rerouted_even_when_on() -> None:
    on = _fit(
        _points_from(_CLEAN_TRUE, _DENSE_KS),
        config=_base_config(reroute_railed_dense_slice=True),
    )
    assert on.method == METHOD_SVI
    assert not genuine_degeneracy_reasons(on)


def test_thin_railed_slice_uses_existing_sparse_fallback_not_the_reroute() -> None:
    # 3 < min_points_per_slice(5): never reaches the SVI/reroute path.
    thin = (-0.12, 0.0, 0.10)
    points = _points_from(_RAILED_UP_TRUE, thin)
    off = _fit(points, config=_base_config())
    on = _fit(points, config=_base_config(reroute_railed_dense_slice=True))
    assert off.method == METHOD_NONPARAMETRIC
    assert on == off  # the reroute knob does not touch the thin path
    assert on.svi is None
    assert on.bound_hits == ()
