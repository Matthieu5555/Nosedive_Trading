from __future__ import annotations

import math
from datetime import UTC, date, datetime

import pytest
from algotrading.core.config import MonetizationConfig
from algotrading.core.provenance import source_ref, stamp
from algotrading.infra.contracts import (
    SURFACE_SIDE_COMBINED,
    Basket,
    BasketLeg,
    ContractValidationError,
    IvDiagnostics,
    IvPoint,
    ProjectedOptionAnalytics,
)
from algotrading.infra.qc import STATUS_FAIL, STATUS_PASS, check_put_call_iv_spread
from algotrading.infra.risk import basket_risk
from algotrading.infra.surfaces import (
    PINNED_TENORS,
    ProjectionConfig,
    SliceFit,
    SnapshotMarketState,
    fit_slice,
    project_grid,
    put_call_iv_spread,
)
from algotrading.infra.surfaces.projection import tenor_years
from fixtures.library import SURFACE_CONFIG

TS = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)
EXPIRY = date(2027, 5, 29)
SPOT = 100.0
CONFIG_HASHES = {"universe": "u-hash", "pricing": "p-hash"}
_MATURITIES = (0.05, 0.25, 1.0, 3.0)


def _slice_at(sigma: float, maturity: float) -> SliceFit:
    ks = (-0.30, -0.20, -0.10, 0.0, 0.10, 0.20, 0.30)
    points = []
    for k in ks:
        strike = SPOT * math.exp(k)
        local_sigma = sigma * (1.0 + 0.10 * k * k)
        total_variance = local_sigma * local_sigma * maturity
        key = f"AAPL|OPT|C|{maturity:.4f}|{strike:g}"
        provenance = stamp(
            calc_ts=TS, code_version="iv-1", config_hashes={"cfg": "c"},
            source_records=(source_ref("market_state_snapshots", TS, key),),
            source_timestamps=(TS,),
        )
        points.append(IvPoint(
            snapshot_ts=TS, contract_key=key, implied_vol=local_sigma,
            log_moneyness=k, total_variance=total_variance, solver_version="iv-1",
            diagnostics=IvDiagnostics(converged=True, iterations=5, residual=1e-12,
                                      status="converged"),
            source_snapshot_ts=TS, provenance=provenance,
        ))
    return fit_slice(
        "AAPL", maturity, tuple(points),
        expiry_date=EXPIRY, day_count="ACT/365", config=SURFACE_CONFIG,
    )


def _surface(sigma: float) -> tuple[SliceFit, ...]:
    return tuple(_slice_at(sigma, m) for m in _MATURITIES)


def _market() -> SnapshotMarketState:
    return SnapshotMarketState(
        underlying="AAPL", provider="IBKR", spot=SPOT,
        discount_factors={tenor_years(t): 1.0 for t in PINNED_TENORS},
        default_discount_factor=1.0,
    )


def _project(
    combined: tuple[SliceFit, ...],
    *,
    put: tuple[SliceFit, ...] = (),
    call: tuple[SliceFit, ...] = (),
):
    return project_grid(
        combined, _market(), put_slices=put, call_slices=call,
        snapshot_ts=TS, source_snapshot_ts=TS, calc_ts=TS,
        projection=ProjectionConfig(version="proj-test"),
        monetization=MonetizationConfig(version="mon-test"),
        config_hashes=CONFIG_HASHES,
    )


def _cell_key(c: ProjectedOptionAnalytics) -> tuple[str, str]:
    return (c.tenor_label, c.delta_band)


def test_surface_side_defaults_to_combined() -> None:
    row = _project(_surface(0.20)).cells[0]
    assert row.surface_side == SURFACE_SIDE_COMBINED


def test_bad_surface_side_is_rejected() -> None:
    base = _project(_surface(0.20)).cells[0]
    with pytest.raises(ContractValidationError):
        ProjectedOptionAnalytics(
            **{**{f.name: getattr(base, f.name) for f in base.__dataclass_fields__.values()},
               "surface_side": "bogus"}
        )


def test_no_per_side_input_emits_only_combined() -> None:
    result = _project(_surface(0.20))
    assert result.cells
    assert {c.surface_side for c in result.cells} == {SURFACE_SIDE_COMBINED}


def test_combined_rows_are_byte_identical_with_or_without_wings() -> None:
    combined = _surface(0.21)
    base = {_cell_key(c): c for c in _project(combined).cells}
    with_wings = {
        _cell_key(c): c
        for c in _project(combined, put=_surface(0.27), call=_surface(0.16)).cells
        if c.surface_side == SURFACE_SIDE_COMBINED
    }
    assert set(base) == set(with_wings)
    for key, before in base.items():
        assert before == with_wings[key], key


def test_each_cell_emits_three_sides_at_the_same_strike() -> None:
    result = _project(_surface(0.21), put=_surface(0.27), call=_surface(0.16))
    by_cell: dict[tuple[str, str], dict[str, ProjectedOptionAnalytics]] = {}
    for c in result.cells:
        by_cell.setdefault(_cell_key(c), {})[c.surface_side] = c
    full = [sides for sides in by_cell.values() if set(sides) == {"put", "call", "combined"}]
    assert full
    for sides in full:
        strikes = {round(s.strike, 10) for s in sides.values()}
        ks = {round(s.log_moneyness, 12) for s in sides.values()}
        assert len(strikes) == 1, strikes
        assert len(ks) == 1, ks


def test_put_wing_iv_exceeds_call_wing_iv_at_every_cell() -> None:
    result = _project(_surface(0.21), put=_surface(0.27), call=_surface(0.16))
    by_cell: dict[tuple[str, str], dict[str, ProjectedOptionAnalytics]] = {}
    for c in result.cells:
        by_cell.setdefault(_cell_key(c), {})[c.surface_side] = c
    checked = 0
    for sides in by_cell.values():
        if set(sides) != {"put", "call", "combined"}:
            continue
        assert sides["put"].implied_vol > sides["combined"].implied_vol > sides["call"].implied_vol
        checked += 1
    assert checked > 0


def test_put_call_spread_is_put_minus_call_per_cell() -> None:
    result = _project(_surface(0.21), put=_surface(0.27), call=_surface(0.16))
    by_cell: dict[tuple[str, str], dict[str, ProjectedOptionAnalytics]] = {}
    for c in result.cells:
        by_cell.setdefault(_cell_key(c), {})[c.surface_side] = c

    spreads = put_call_iv_spread(result.cells)
    spread_by_cell = {(s.tenor_label, s.delta_band): s for s in spreads}

    expected_cells = {k for k, v in by_cell.items() if {"put", "call"} <= set(v)}
    assert set(spread_by_cell) == expected_cells
    assert expected_cells

    for key, point in spread_by_cell.items():
        put_cell, call_cell = by_cell[key]["put"], by_cell[key]["call"]
        assert point.put_iv == pytest.approx(put_cell.implied_vol)
        assert point.call_iv == pytest.approx(call_cell.implied_vol)
        assert point.iv_spread == pytest.approx(put_cell.implied_vol - call_cell.implied_vol)
        assert point.strike == pytest.approx(put_cell.strike)
        assert point.iv_spread > 0.0


def test_spread_skips_one_sided_cells() -> None:
    spreads = put_call_iv_spread(_project(_surface(0.20)).cells)
    assert spreads == ()


def test_spread_qc_passes_within_bound() -> None:
    spreads = put_call_iv_spread(
        _project(_surface(0.21), put=_surface(0.27), call=_surface(0.16)).cells
    )
    result = check_put_call_iv_spread(
        spreads, "AAPL", max_abs_spread=0.50,
        threshold_version="qc-test", run_id="run-1", run_ts=TS,
    )
    assert result.qc_status == STATUS_PASS
    assert result.measured_value == 0.0


def test_spread_qc_fails_on_blowout() -> None:
    spreads = put_call_iv_spread(
        _project(_surface(0.21), put=_surface(0.40), call=_surface(0.12)).cells
    )
    result = check_put_call_iv_spread(
        spreads, "AAPL", max_abs_spread=0.05,
        threshold_version="qc-test", run_id="run-1", run_ts=TS,
    )
    assert result.qc_status == STATUS_FAIL
    assert result.measured_value == float(len(spreads))
    assert result.measured_value > 0.0


def test_basket_risk_ignores_per_side_rows() -> None:
    result = _project(_surface(0.21), put=_surface(0.27), call=_surface(0.16))
    cell = next(c for c in result.cells if c.surface_side == SURFACE_SIDE_COMBINED)
    leg = BasketLeg(
        instrument_kind="option", side="long", quantity=1.0,
        underlying="AAPL", tenor_label=cell.tenor_label, delta_band=cell.delta_band,
    )
    basket = Basket(
        basket_id="b1", trade_date=date(2026, 5, 29), underlying="AAPL", legs=(leg,),
    )
    combined_only = [c for c in result.cells if c.surface_side == SURFACE_SIDE_COMBINED]
    risk_combined = basket_risk(
        basket, analytics_rows=combined_only, spot_by_underlying={"AAPL": SPOT},
    )
    risk_all = basket_risk(
        basket, analytics_rows=result.cells, spot_by_underlying={"AAPL": SPOT},
    )
    assert risk_all.dollar_delta == risk_combined.dollar_delta
    assert risk_all.gaps == ()
