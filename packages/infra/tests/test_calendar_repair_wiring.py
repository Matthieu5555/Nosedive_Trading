"""The calendar-repair override is wired through the served grid and the calendar QC (ADR 0062).

The floor policy itself is covered in ``test_calendar_variance_repair``. Here we prove the override
it produces actually reaches:
  * the persisted ``surface_grid`` marks (via ``surface_grid_cells``), and
  * the calendar QC's view of each slice (via the driver's ``_calendar_slice_of``),
so the stored surface and its check read the same repaired numbers, while the default (no override)
path is byte-for-byte the raw fit.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from algotrading.infra.actor.driver import _calendar_slice_of
from algotrading.infra.iv import iv_point, solve_iv
from algotrading.infra.surfaces import fit_slice, surface_grid_cells
from fixtures.library import SURFACE_CONFIG
from fixtures.synthetic import build_synthetic_surface

TS = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)
EXPIRY = date(2026, 6, 19)
CONFIG_HASH = {"cfg": "cfg-hash-repair"}


def _synthetic_fit():
    from algotrading.core.config import SolverConfig

    solver = SolverConfig(version="iv-1", iv_tolerance=1e-12, max_iterations=200)
    surface = build_synthetic_surface()
    iv_points = []
    for p in surface.points:
        result = solve_iv(
            p.call_price, contract_key=f"AAPL|OPT|C|{p.strike:g}", forward=surface.forward,
            strike=p.strike, maturity_years=surface.maturity_years,
            discount_factor=surface.discount_factor, option_right="C", config=solver,
        )
        iv_points.append(
            iv_point(result, snapshot_ts=TS, source_snapshot_ts=TS, calc_ts=TS,
                     config_hashes=CONFIG_HASH)
        )
    return fit_slice(
        "AAPL", surface.maturity_years, tuple(iv_points),
        expiry_date=EXPIRY, day_count="ACT/365", config=SURFACE_CONFIG,
    )


def _cells(fit, buckets, override=None):
    return surface_grid_cells(
        fit, buckets, snapshot_ts=TS, source_snapshot_ts=TS, calc_ts=TS,
        config_hashes=CONFIG_HASH, total_variance_by_bucket=override,
    )


def test_no_override_serves_the_raw_fit() -> None:
    fit = _synthetic_fit()
    buckets = (-0.1, 0.0, 0.1)
    cells = _cells(fit, buckets)
    for cell in cells:
        assert cell.total_variance == pytest.approx(
            max(fit.total_variance(cell.moneyness_bucket), 0.0), rel=1e-12
        )


def test_override_replaces_only_the_named_buckets() -> None:
    fit = _synthetic_fit()
    buckets = (-0.1, 0.0, 0.1)
    bumped = max(fit.total_variance(0.1), 0.0) + 0.01
    cells = {c.moneyness_bucket: c.total_variance for c in _cells(fit, buckets, {0.1: bumped})}
    assert cells[0.1] == pytest.approx(bumped, rel=1e-12)  # overridden
    assert cells[0.0] == pytest.approx(max(fit.total_variance(0.0), 0.0), rel=1e-12)  # raw
    assert cells[-0.1] == pytest.approx(max(fit.total_variance(-0.1), 0.0), rel=1e-12)  # raw


def test_override_is_still_floored_at_zero() -> None:
    fit = _synthetic_fit()
    cells = {c.moneyness_bucket: c.total_variance for c in _cells(fit, (0.0,), {0.0: -1.0})}
    assert cells[0.0] == 0.0  # negative total variance is clamped, as for the raw path


def test_calendar_slice_reads_the_override_on_grid_and_falls_back_off_grid() -> None:
    fit = _synthetic_fit()
    override = {0.0: 0.123, 0.1: 0.456}
    slice_ = _calendar_slice_of(fit, override)
    # On the grid the QC sees the repaired number...
    assert slice_.total_variance(0.0) == 0.123
    assert slice_.total_variance(0.1) == 0.456
    # ...and off the grid it falls back to the raw fit (the QC only queries grid points anyway).
    off_grid = 0.05
    assert slice_.total_variance(off_grid) == fit.total_variance(off_grid)


def test_calendar_slice_without_override_is_the_raw_fit() -> None:
    fit = _synthetic_fit()
    slice_ = _calendar_slice_of(fit)
    for k in (-0.1, 0.0, 0.1):
        assert slice_.total_variance(k) == fit.total_variance(k)
