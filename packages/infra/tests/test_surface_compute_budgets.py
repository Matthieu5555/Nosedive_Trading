"""Compute-time budgets for the two surface hot paths the batch-2 budgets missed.

``test_boundary_business_budgets.py`` put wall-clock budgets on pricing, IV inversion,
SVI calibration, the scenario reprice, and the book finite-difference Greeks. It left the
**surface engine's own two hot paths uncovered**, and they are the heaviest analytics work
per snapshot:

* :func:`project_grid` — the 1F entrypoint that, for every pinned tenor × delta-band cell,
  inverts an option delta to a strike (a bisection on the fitted smile) and prices the cell
  with Black-76. On the broad term surface this is ~200 cells, ~23 ms on a dev box — an
  order of magnitude over any other single analytics call, and the one most exposed to an
  algorithmic regression in the per-cell strike solve.
* :func:`reconstruct_dense_surface` — the dense ``n_moneyness × n_maturities`` surface grid
  the front renders, ``O(n_m · n_mat)`` SVI evaluations.

Both run once per underlying per snapshot, so a 10×+ blow-up in either silently lengthens
every capture. Each budget here is paired with an **independent-oracle** correctness assert
(option theory for the projection cells; the raw-SVI ``w(k)`` of Eq 20 hand-coded for the
dense grid), so a path that gets "fast" by getting wrong fails too.

Budgets are regression tripwires set at ≈25-40× the measured compute floor on a dev box
(floors, in ms: project_grid ≈ 23, reconstruct 60×60 ≈ 1.7). That headroom absorbs a
slower/loaded CI box and a cold first run, yet a 25×+ algorithmic blow-up still trips them.
Timing is best-of-N so scheduler jitter cannot trip it; only the genuine compute floor counts.

Independent oracle, never the code under test: the raw-SVI total variance
``w(k) = a + b(rho*(k-m) + sqrt((k-m)^2 + sigma^2))`` (Eq 20) is computed by hand below.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import pytest
from algotrading.core.config import MonetizationConfig
from algotrading.core.provenance import source_ref, stamp
from algotrading.infra.contracts import (
    IvDiagnostics,
    IvPoint,
    SurfaceParameters,
)
from algotrading.infra.contracts.bundles import SurfaceFitDiagnostics
from algotrading.infra.surfaces import (
    SliceFit,
    SnapshotMarketState,
    fit_slice,
    project_grid,
    reconstruct_dense_surface,
)
from algotrading.infra.surfaces.projection import PINNED_TENORS, ProjectionConfig, tenor_years
from fixtures.library import SURFACE_CONFIG
from fixtures.records import EXPIRY as REC_EXPIRY
from fixtures.records import SNAPSHOT_TS, make_stamp
from fixtures.synthetic import SyntheticTermSurface, build_synthetic_term_surface

TS = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)
EXPIRY = date(2026, 6, 19)
CONFIG_HASHES = {"universe": "u-hash", "pricing": "p-hash"}

# Floors measured on a dev box (ms): project_grid ≈ 23, reconstruct 60×60 ≈ 1.7. Budgets are
# the regression tripwires at ≈25-40× that floor — generous enough for a loaded CI box and a
# cold first run, tight enough that a 25×+ blow-up in the per-cell strike solve or the dense
# sampling trips them. Each is paired with a correctness oracle in its test.
BUDGET_PROJECT_GRID_S = 0.60
BUDGET_RECONSTRUCT_DENSE_S = 0.10


# ---------------------------------------------------------------------------
# Timing harness (best-of-N floor), mirroring test_boundary_business_budgets.
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Timed[T]:
    result: T
    seconds: float


def time_best_of[T](work: Callable[[], T], *, repeats: int = 5) -> Timed[T]:
    """Run ``work`` ``repeats`` times and keep the fastest run (the true compute floor)."""
    start = time.perf_counter()
    result = work()
    best = time.perf_counter() - start
    for _ in range(repeats - 1):
        start = time.perf_counter()
        result = work()
        best = min(best, time.perf_counter() - start)
    return Timed(result=result, seconds=best)


def _svi_w(k: float, a: float, b: float, rho: float, m: float, sigma: float) -> float:
    """Raw-SVI total variance (Eq 20) — the independent oracle for the dense grid."""
    x = k - m
    return a + b * (rho * x + math.sqrt(x * x + sigma * sigma))


# ---------------------------------------------------------------------------
# project_grid inputs, built through the real surface engine (fit_slice).
# ---------------------------------------------------------------------------
def _iv_points_for_slice(surface: Any, underlying: str) -> tuple[IvPoint, ...]:
    points = []
    for p in surface.points:
        key = f"{underlying}|OPT|C|{surface.maturity_years:.4f}|{p.strike:g}"
        a_stamp = stamp(
            calc_ts=TS, code_version="iv-1", config_hashes={"cfg": "c"},
            source_records=(source_ref("market_state_snapshots", TS, key),),
            source_timestamps=(TS,),
        )
        points.append(IvPoint(
            snapshot_ts=TS, contract_key=key, implied_vol=p.sigma,
            log_moneyness=p.log_moneyness, total_variance=p.total_variance, solver_version="iv-1",
            diagnostics=IvDiagnostics(converged=True, iterations=5, residual=1e-12, status="converged"),
            source_snapshot_ts=TS, provenance=a_stamp,
        ))
    return tuple(points)


def _fit_term_surface(term: SyntheticTermSurface, underlying: str = "AAPL") -> tuple[SliceFit, ...]:
    return tuple(
        fit_slice(
            underlying, s.maturity_years, _iv_points_for_slice(s, underlying),
            expiry_date=EXPIRY, day_count="ACT/365", config=SURFACE_CONFIG,
        )
        for s in term.slices
    )


def _market(term: SyntheticTermSurface, underlying: str = "AAPL") -> SnapshotMarketState:
    return SnapshotMarketState(
        underlying=underlying, provider="DERIBIT", spot=term.forward,
        discount_factors={tenor_years(label): math.exp(-term.rate * tenor_years(label))
                          for label in PINNED_TENORS},
        default_discount_factor=1.0,
    )


def test_project_grid_within_budget_and_cells_are_consistent() -> None:
    """The full tenor×band projection runs under budget AND its cells obey option theory.

    Correctness oracle (independent of the projection code): on the ATM straddle the call and
    put pillars must land on the same strike and carry near-opposite spot deltas — a property
    of option pricing, not of the projector. A path that got fast by mis-solving the strike
    would break this, so the budget cannot be passed by getting the answer wrong.
    """
    term = build_synthetic_term_surface()
    slices = _fit_term_surface(term)
    market = _market(term)
    projection = ProjectionConfig(version="proj-budget")
    monetization = MonetizationConfig(version="mon-budget")

    def run() -> Any:
        return project_grid(
            slices, market, snapshot_ts=TS, source_snapshot_ts=TS, calc_ts=TS,
            projection=projection, monetization=monetization, config_hashes=CONFIG_HASHES,
        )

    timed = time_best_of(run)
    result = timed.result
    # Real work happened: the broad surface fills a meaningful slab of the grid.
    assert len(result.cells) > 100
    # Independent oracle: the ATM straddle's two pillars share a strike; their deltas oppose.
    atm = next(c for c in result.cells if c.tenor_label == "12m" and c.delta_band == "atm")
    atmp = next(c for c in result.cells if c.tenor_label == "12m" and c.delta_band == "atmp")
    assert atmp.strike == pytest.approx(atm.strike)
    assert atm.delta == pytest.approx(-atmp.delta, abs=0.02)
    assert timed.seconds < BUDGET_PROJECT_GRID_S, (
        f"project_grid took {timed.seconds:.4f}s, over budget {BUDGET_PROJECT_GRID_S}s"
    )


# ---------------------------------------------------------------------------
# reconstruct_dense_surface inputs: persisted SVI slices with known params.
# ---------------------------------------------------------------------------
def _slice(maturity_years: float, *, a: float, b: float, rho: float, m: float, sigma: float) -> SurfaceParameters:
    return SurfaceParameters(
        snapshot_ts=SNAPSHOT_TS, underlying="AAA", maturity_years=maturity_years,
        model_version="svi-test", svi_a=a, svi_b=b, svi_rho=rho, svi_m=m, svi_sigma=sigma,
        expiry_date=REC_EXPIRY, day_count="ACT/365",
        diagnostics=SurfaceFitDiagnostics(rmse=0.001, n_points=8, arb_free=True, bound_hits=(), converged=True),
        source_snapshot_ts=SNAPSHOT_TS, provenance=make_stamp(),
    )


# Eight fitted slices (the pinned tenor count), each a known SVI smile, increasing in variance.
_BENCH_SLICES = tuple(
    _slice(T, a=0.04 + 0.0035 * i, b=0.10, rho=-0.30, m=0.0, sigma=0.20)
    for i, T in enumerate((0.08, 0.25, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0))
)
_P_LO = dict(a=0.04, b=0.10, rho=-0.30, m=0.0, sigma=0.20)  # the T=0.08 slice (i=0)


def test_reconstruct_dense_surface_within_budget_and_endpoint_is_the_slice() -> None:
    """A 60×60 dense reconstruction runs under budget AND its first row is the fitted slice.

    Correctness oracle (independent): at the lowest fitted maturity the row must be exactly
    that slice's curve, ``IV(k) = sqrt(w(k) / T)`` with ``w`` the raw-SVI Eq 20 hand-coded
    here. A reconstruction that sampled the wrong slice or mis-interpolated would fail this.
    """
    slices = list(_BENCH_SLICES)

    def run() -> Any:
        return reconstruct_dense_surface(slices, n_moneyness=60, n_maturities=60)

    timed = time_best_of(run)
    surface = timed.result
    assert surface is not None
    assert len(surface.implied_vol) == 60 and all(len(row) == 60 for row in surface.implied_vol)
    # Endpoint row == the lowest-maturity slice sampled: IV(k) = sqrt(w(k)/T), T = 0.08.
    t_lo = 0.08
    for j, k in enumerate(surface.log_moneyness):
        assert surface.implied_vol[0][j] == pytest.approx(math.sqrt(_svi_w(k, **_P_LO) / t_lo))
    assert timed.seconds < BUDGET_RECONSTRUCT_DENSE_S, (
        f"reconstruct_dense_surface took {timed.seconds:.4f}s, over budget {BUDGET_RECONSTRUCT_DENSE_S}s"
    )
