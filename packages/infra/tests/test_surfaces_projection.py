"""Tests for `surfaces.project_surface_fit` — the fit→contract projection seam.

The projection owns the rule about which fit method emits which persisted contract.
These tests pin that rule directly, independent of the actor that calls it: an SVI fit
yields parameters and a grid, a nonparametric fallback yields only a grid, and an
insufficient slice yields nothing. Expected cell counts are derived from the number of
moneyness buckets, never read back from the function under test.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime

from algotrading.core.provenance import source_ref, stamp
from algotrading.infra.contracts import IvDiagnostics, IvPoint
from algotrading.infra.surfaces import (
    METHOD_INSUFFICIENT,
    METHOD_NONPARAMETRIC,
    METHOD_SVI,
    SliceFit,
    SurfaceProjection,
    fit_slice,
    project_surface_fit,
)
from fixtures.library import SURFACE_CONFIG
from fixtures.synthetic import build_synthetic_surface

_TS = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)
_EXPIRY = date(2026, 6, 19)
_SURFACE = build_synthetic_surface()  # F=100, DF=0.99, T=0.25, >=5 distinct strikes
_BUCKETS = (-0.2, -0.1, 0.0, 0.1, 0.2)


def _iv_point(k: float, w: float, key: str) -> IvPoint:
    a_stamp = stamp(
        calc_ts=_TS, code_version="iv-1", config_hashes={"cfg": "c"},
        source_records=(source_ref("market_state_snapshots", _TS, key),),
        source_timestamps=(_TS,),
    )
    iv = math.sqrt(w / _SURFACE.maturity_years) if w > 0 else 0.0
    return IvPoint(
        snapshot_ts=_TS, contract_key=key, implied_vol=iv, log_moneyness=k, total_variance=w, solver_version="iv-1",
        diagnostics=IvDiagnostics(converged=True, iterations=5, residual=1e-12, status="converged"),
        source_snapshot_ts=_TS, provenance=a_stamp,
    )


def _fit(points: tuple[IvPoint, ...]) -> SliceFit:
    return fit_slice(
        "AAPL", _SURFACE.maturity_years, points,
        expiry_date=_EXPIRY, day_count="ACT/365", config=SURFACE_CONFIG,
    )


def _project(points: tuple[IvPoint, ...]) -> SurfaceProjection:
    return project_surface_fit(
        _fit(points), _BUCKETS,
        snapshot_ts=_TS, source_snapshot_ts=_TS, calc_ts=_TS, config_hashes={"cfg": "cfg"},
    )


def test_svi_fit_projects_parameters_and_a_full_grid() -> None:
    points = tuple(
        _iv_point(p.log_moneyness, p.total_variance, f"K{p.strike:g}")
        for p in _SURFACE.points
    )
    assert _fit(points).method == METHOD_SVI  # guard: enough distinct strikes for SVI

    projection = _project(points)

    assert projection.parameters is not None
    assert projection.parameters.svi_a == projection.parameters.svi_a  # finite, not NaN
    # One cell per moneyness bucket.
    assert len(projection.grid_cells) == len(_BUCKETS)
    assert tuple(c.moneyness_bucket for c in projection.grid_cells) == _BUCKETS


def test_nonparametric_fit_projects_a_grid_but_no_parameters() -> None:
    # Two distinct strikes: below the SVI minimum, so a labeled nonparametric fallback.
    points = (_iv_point(-0.1, 0.04, "K90"), _iv_point(0.1, 0.05, "K110"))
    assert _fit(points).method == METHOD_NONPARAMETRIC  # guard

    projection = _project(points)

    assert projection.parameters is None  # no SVI model to persist
    assert len(projection.grid_cells) == len(_BUCKETS)  # but a usable grid still emitted


def test_insufficient_fit_projects_nothing() -> None:
    assert _fit(()).method == METHOD_INSUFFICIENT  # guard

    projection = _project(())

    assert projection.parameters is None
    assert projection.grid_cells == ()
