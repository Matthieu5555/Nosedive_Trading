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

BUDGET_PROJECT_GRID_S = 0.60
BUDGET_RECONSTRUCT_DENSE_S = 0.10


@dataclass(frozen=True, slots=True)
class Timed[T]:
    result: T
    seconds: float


def time_best_of[T](work: Callable[[], T], *, repeats: int = 5) -> Timed[T]:
    start = time.perf_counter()
    result = work()
    best = time.perf_counter() - start
    for _ in range(repeats - 1):
        start = time.perf_counter()
        result = work()
        best = min(best, time.perf_counter() - start)
    return Timed(result=result, seconds=best)


def _svi_w(k: float, a: float, b: float, rho: float, m: float, sigma: float) -> float:
    x = k - m
    return a + b * (rho * x + math.sqrt(x * x + sigma * sigma))


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
    assert len(result.cells) > 100
    atm = next(c for c in result.cells if c.tenor_label == "12m" and c.delta_band == "atm")
    atmp = next(c for c in result.cells if c.tenor_label == "12m" and c.delta_band == "atmp")
    assert atmp.strike == pytest.approx(atm.strike)
    assert atm.delta == pytest.approx(-atmp.delta, abs=0.02)
    assert timed.seconds < BUDGET_PROJECT_GRID_S, (
        f"project_grid took {timed.seconds:.4f}s, over budget {BUDGET_PROJECT_GRID_S}s"
    )


def _slice(maturity_years: float, *, a: float, b: float, rho: float, m: float, sigma: float) -> SurfaceParameters:
    return SurfaceParameters(
        snapshot_ts=SNAPSHOT_TS, underlying="AAA", maturity_years=maturity_years,
        model_version="svi-test", svi_a=a, svi_b=b, svi_rho=rho, svi_m=m, svi_sigma=sigma,
        expiry_date=REC_EXPIRY, day_count="ACT/365",
        diagnostics=SurfaceFitDiagnostics(rmse=0.001, n_points=8, arb_free=True, bound_hits=(), converged=True),
        source_snapshot_ts=SNAPSHOT_TS, provenance=make_stamp(),
    )


_BENCH_SLICES = tuple(
    _slice(T, a=0.04 + 0.0035 * i, b=0.10, rho=-0.30, m=0.0, sigma=0.20)
    for i, T in enumerate((0.08, 0.25, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0))
)
_P_LO = dict(a=0.04, b=0.10, rho=-0.30, m=0.0, sigma=0.20)


def test_reconstruct_dense_surface_within_budget_and_endpoint_is_the_slice() -> None:
    slices = list(_BENCH_SLICES)

    def run() -> Any:
        return reconstruct_dense_surface(slices, n_moneyness=60, n_maturities=60)

    timed = time_best_of(run)
    surface = timed.result
    assert surface is not None
    assert len(surface.implied_vol) == 60 and all(len(row) == 60 for row in surface.implied_vol)
    t_lo = 0.08
    for j, k in enumerate(surface.log_moneyness):
        assert surface.implied_vol[0][j] == pytest.approx(math.sqrt(_svi_w(k, **_P_LO) / t_lo))
    assert timed.seconds < BUDGET_RECONSTRUCT_DENSE_S, (
        f"reconstruct_dense_surface took {timed.seconds:.4f}s, over budget {BUDGET_RECONSTRUCT_DENSE_S}s"
    )
