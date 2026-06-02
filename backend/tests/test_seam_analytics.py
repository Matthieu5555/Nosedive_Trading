"""C -> A seam: every contract C emits round-trips through A's store, malformed rejected.

The architecture's bet is that A's typed contracts are the only objects crossing a
workstream line, so C (the consumer) owns the test proving its six emitted contracts
survive A's write/read path and that A's write-ahead validation refuses a malformed
one — checked now, by C, not weeks later in E's integration (per ``tasks/TESTING.md``).

Every contract is produced by C's *real* code, not hand-built, so this doubles as an
end-to-end check of the analytics pipeline: synthetic chain -> snapshot, forward,
IV, surface, and a price, each stamped and projected to its contract. Each gets a
happy round-trip (write -> read -> equal) and a malformed instance that A rejects.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from config import QcThresholdConfig, SolverConfig
from contracts import (
    ContractValidationError,
    ForwardCurvePoint,
    IvPoint,
    MarketStateSnapshot,
    PricingResult,
    SurfaceGrid,
    SurfaceParameters,
)
from fixtures.events import UNDERLYING, quote_events
from fixtures.synthetic import build_synthetic_surface
from forwards import ForwardPair, estimate_forward, forward_curve_point
from iv import iv_point, solve_iv
from pricing import PRICER_VERSION, from_forward, price, pricing_result
from provenance import source_ref, stamp
from snapshots import SnapshotContext, build_snapshot
from storage import ParquetStore
from surfaces import fit_slice, surface_grid_cells, surface_parameters

TS = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)
EXPIRY = date(2026, 6, 19)
SURFACE = build_synthetic_surface()  # F=100, DF=0.99, T=0.25
SOLVER = SolverConfig(version="iv-1", iv_tolerance=1e-12, max_iterations=200)


def _qc() -> QcThresholdConfig:
    return QcThresholdConfig(
        version="qc-1", max_spread_pct=0.5, max_quote_age_seconds=30.0, min_chain_count=1
    )


# -- C's six contracts, each produced by C's real code ----------------------
def make_snapshot() -> MarketStateSnapshot:
    events = quote_events(UNDERLYING, bid=190.4, ask=190.6, last=190.5, ts=TS)
    context = SnapshotContext(snapshot_ts=TS, qc=_qc(), calc_ts=TS, config_hash="cfg-hash-0")
    return build_snapshot(UNDERLYING, events, context=context)


def _forward_pairs() -> tuple[ForwardPair, ...]:
    return tuple(
        ForwardPair(strike=p.strike, call_mid=p.call_price, put_mid=p.put_price, liquidity=1.0,
                    call_key=f"AAPL|OPT|C|{p.strike:g}", put_key=f"AAPL|OPT|P|{p.strike:g}")
        for p in SURFACE.points
    )


def make_forward_point() -> ForwardCurvePoint:
    estimate = estimate_forward("AAPL", SURFACE.maturity_years, _forward_pairs(),
                                spot=SURFACE.forward * SURFACE.discount_factor)
    return forward_curve_point(estimate, snapshot_ts=TS, expiry_date=EXPIRY, day_count="ACT/365",
                               source_snapshot_ts=TS, calc_ts=TS, config_hash="cfg-hash-0")


def make_iv_points() -> list[IvPoint]:
    points = []
    for p in SURFACE.points:
        result = solve_iv(p.call_price, contract_key=f"AAPL|OPT|C|{p.strike:g}",
                          forward=SURFACE.forward, strike=p.strike,
                          maturity_years=SURFACE.maturity_years,
                          discount_factor=SURFACE.discount_factor, option_right="C", config=SOLVER)
        points.append(iv_point(result, snapshot_ts=TS, source_snapshot_ts=TS, calc_ts=TS,
                               config_hash="cfg-hash-0"))
    return points


def make_surface_parameters() -> SurfaceParameters:
    fit = fit_slice("AAPL", SURFACE.maturity_years, tuple(make_iv_points()),
                    expiry_date=EXPIRY, day_count="ACT/365")
    return surface_parameters(fit, snapshot_ts=TS, source_snapshot_ts=TS, calc_ts=TS,
                              config_hash="cfg-hash-0")


def make_surface_grid() -> SurfaceGrid:
    fit = fit_slice("AAPL", SURFACE.maturity_years, tuple(make_iv_points()),
                    expiry_date=EXPIRY, day_count="ACT/365")
    cells = surface_grid_cells(fit, (-0.1, 0.0, 0.1), snapshot_ts=TS, source_snapshot_ts=TS,
                               calc_ts=TS, config_hash="cfg-hash-0")
    return cells[0]


def make_pricing_result() -> PricingResult:
    state = from_forward(forward=100.0, strike=100.0, maturity_years=0.25, volatility=0.2,
                         discount_factor=0.99, option_right="C")
    greeks = price(state)
    a_stamp = stamp(calc_ts=TS, code_version=PRICER_VERSION, config_hash="cfg-hash-0",
                    source_records=(source_ref("market_state_snapshots", TS, "AAPL|OPT|C|100"),),
                    source_timestamps=(TS,))
    return pricing_result(state, greeks, snapshot_ts=TS, contract_key="AAPL|OPT|C|100",
                          source_snapshot_ts=TS, provenance=a_stamp)


# (table, factory, malformed-field, broken-value) for each of C's six contracts.
_CASES = [
    ("market_state_snapshots", make_snapshot, "reference_spot", 0.0),
    ("forward_curve", make_forward_point, "forward", -1.0),
    ("iv_points", make_iv_points, "iv", -1.0),
    ("surface_parameters", make_surface_parameters, "svi_b", 0.0),
    ("surface_grid", make_surface_grid, "total_variance", -1.0),
    ("pricing_results", make_pricing_result, "vega", -1.0),
]


def _one(factory: Callable[[], Any]) -> Any:
    produced = factory()
    return produced[0] if isinstance(produced, list) else produced


# -- happy round-trips: every emitted contract survives write -> read -> equal
@pytest.mark.parametrize("table, factory", [(t, f) for t, f, _, _ in _CASES])
def test_contract_round_trips_through_a_storage(
    table: str, factory: Callable[[], Any], tmp_path: Path
) -> None:
    store = ParquetStore(tmp_path)
    record = _one(factory)
    store.write(table, [record])
    read_back = store.read(table)
    assert read_back == [record]
    # The provenance stamp survived intact (the determinism handle round-trips).
    assert read_back[0].provenance.stamp_hash == record.provenance.stamp_hash


# -- A's write-ahead validation refuses one malformed instance per contract
@pytest.mark.parametrize(
    "table, factory, field, bad_value", _CASES,
    ids=[t for t, _, _, _ in _CASES],
)
def test_malformed_contract_is_rejected_by_a_validation(
    table: str, factory: Callable[[], Any], field: str, bad_value: float, tmp_path: Path
) -> None:
    store = ParquetStore(tmp_path)
    malformed = dataclasses.replace(_one(factory), **{field: bad_value})
    with pytest.raises(ContractValidationError) as info:
        store.write(table, [malformed])
    assert info.value.field == field


def test_a_nan_greek_is_rejected(tmp_path: Path) -> None:
    # A NaN is not a number to coerce; A must reject it at the write door.
    store = ParquetStore(tmp_path)
    malformed = dataclasses.replace(make_pricing_result(), delta=math.nan)
    with pytest.raises(ContractValidationError) as info:
        store.write("pricing_results", [malformed])
    assert info.value.field == "delta"


def test_full_iv_chain_round_trips_as_a_batch(tmp_path: Path) -> None:
    # All five solved IV points (a realistic batch) write and read back equal.
    store = ParquetStore(tmp_path)
    points = make_iv_points()
    store.write("iv_points", points)
    by_key = lambda point: point.contract_key  # noqa: E731
    assert sorted(store.read("iv_points"), key=by_key) == sorted(points, key=by_key)
