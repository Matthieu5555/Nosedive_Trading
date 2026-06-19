from __future__ import annotations

import dataclasses
import json
import math
import os
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from algotrading.core.config import MonetizationConfig, canonical_json
from algotrading.core.provenance import source_ref, stamp
from algotrading.infra.contracts import (
    ContractValidationError,
    IvDiagnostics,
    IvPoint,
    spec_for_table,
)
from algotrading.infra.pricing import UNIT_STRINGS, dollar_greeks, from_forward, price_european
from algotrading.infra.storage import ParquetStore
from algotrading.infra.surfaces import (
    PINNED_TENORS,
    SliceFit,
    SnapshotMarketState,
    fit_slice,
    interpolate_total_variance,
    merged_config_hashes,
    project_grid,
    tenor_years,
)
from algotrading.infra.surfaces.projection import (
    ProjectionConfig,
    ProjectionConfigError,
    delta_band_axis,
)
from fixtures.library import SURFACE_CONFIG
from fixtures.synthetic import (
    SyntheticTermSurface,
    build_synthetic_term_surface,
)
from hypothesis import given, settings
from hypothesis import strategies as st

TS = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)
LATER_TS = datetime(2026, 5, 30, 15, 30, tzinfo=UTC)
EXPIRY = date(2026, 6, 19)
CONFIG_HASHES = {"universe": "u-hash", "pricing": "p-hash"}
_GOLDEN_PATH = Path(__file__).parent / "golden" / "projected_option_analytics.json"
_TESTS_DIR = str(Path(__file__).resolve().parent)


def _iv_points_for_slice(
    surface: Any, underlying: str, snapshot_ts: datetime
) -> tuple[IvPoint, ...]:
    points = []
    for p in surface.points:
        key = f"{underlying}|OPT|C|{surface.maturity_years:.4f}|{p.strike:g}"
        a_stamp = stamp(
            calc_ts=snapshot_ts, code_version="iv-1", config_hashes={"cfg": "c"},
            source_records=(source_ref("market_state_snapshots", snapshot_ts, key),),
            source_timestamps=(snapshot_ts,),
        )
        points.append(IvPoint(
            snapshot_ts=snapshot_ts, contract_key=key, implied_vol=p.sigma,
            log_moneyness=p.log_moneyness, total_variance=p.total_variance, solver_version="iv-1",
            diagnostics=IvDiagnostics(converged=True, iterations=5, residual=1e-12,
                                      status="converged"),
            source_snapshot_ts=snapshot_ts, provenance=a_stamp,
        ))
    return tuple(points)


def _fit_term_surface(
    term: SyntheticTermSurface, underlying: str = "AAPL", snapshot_ts: datetime = TS
) -> tuple[SliceFit, ...]:
    return tuple(
        fit_slice(
            underlying, s.maturity_years, _iv_points_for_slice(s, underlying, snapshot_ts),
            expiry_date=EXPIRY, day_count="ACT/365", config=SURFACE_CONFIG,
        )
        for s in term.slices
    )


def _market(term: SyntheticTermSurface, underlying: str = "AAPL",
            provider: str = "DERIBIT") -> SnapshotMarketState:
    return SnapshotMarketState(
        underlying=underlying, provider=provider, spot=term.forward,
        discount_factors={t: math.exp(-term.rate * t) for t in PINNED_TENORS_YEARS()},
        default_discount_factor=1.0,
        spot_is_forward=True,
    )


def PINNED_TENORS_YEARS() -> tuple[float, ...]:
    return tuple(tenor_years(label) for label in PINNED_TENORS)


def _project(
    term: SyntheticTermSurface,
    *,
    projection: ProjectionConfig | None = None,
    monetization: MonetizationConfig | None = None,
    snapshot_ts: datetime = TS,
    underlying: str = "AAPL",
    provider: str = "DERIBIT",
) -> Any:
    slices = _fit_term_surface(term, underlying, snapshot_ts)
    return project_grid(
        slices, _market(term, underlying, provider),
        snapshot_ts=snapshot_ts, source_snapshot_ts=snapshot_ts, calc_ts=snapshot_ts,
        projection=projection or ProjectionConfig(version="proj-test"),
        monetization=monetization or MonetizationConfig(version="mon-test"),
        config_hashes=CONFIG_HASHES,
    )


def test_tenor_grid_is_the_pinned_eight() -> None:
    assert PINNED_TENORS == ("10d", "1m", "3m", "6m", "12m", "18m", "2y", "3y")
    assert ProjectionConfig(version="v").tenor_grid == PINNED_TENORS


def test_tenor_grid_drift_fails_loudly() -> None:
    with pytest.raises(ProjectionConfigError):
        ProjectionConfig(version="v", tenor_grid=("10d", "1m", "3m"))
    with pytest.raises(ProjectionConfigError):
        ProjectionConfig(version="v", tenor_grid=("1m", "10d", "3m", "6m", "12m", "18m", "2y", "3y"))


def test_emitted_cells_carry_only_pinned_tenor_labels() -> None:
    result = _project(build_synthetic_term_surface())
    assert {c.tenor_label for c in result.cells} <= set(PINNED_TENORS)
    seen_tenors = [c.tenor_label for c in result.cells]
    order = {t: i for i, t in enumerate(PINNED_TENORS)}
    assert seen_tenors == sorted(seen_tenors, key=lambda t: order[t])


def test_delta_band_spans_30d_put_to_30d_call() -> None:
    result = _project(build_synthetic_term_surface())
    by_tenor: dict[str, set[str]] = {}
    for c in result.cells:
        by_tenor.setdefault(c.tenor_label, set()).add(c.delta_band)
    assert {"30dp", "atm", "30dc"} <= by_tenor["12m"]


def test_atm_delta_is_near_half() -> None:
    result = _project(build_synthetic_term_surface())
    atm = next(c for c in result.cells if c.tenor_label == "12m" and c.delta_band == "atm")
    assert atm.target_delta == 0.0
    assert 0.45 <= atm.delta <= 0.55


def test_atm_put_pillar_shares_the_atm_call_strike() -> None:
    result = _project(build_synthetic_term_surface())
    atm = next(c for c in result.cells if c.tenor_label == "12m" and c.delta_band == "atm")
    atmp = next(c for c in result.cells if c.tenor_label == "12m" and c.delta_band == "atmp")
    assert atmp.target_delta == 0.0
    assert atmp.strike == pytest.approx(atm.strike)
    assert atmp.log_moneyness == pytest.approx(atm.log_moneyness)
    assert atmp.implied_vol == pytest.approx(atm.implied_vol)


def test_atm_put_pillar_is_a_put_with_matching_gamma_vega() -> None:
    result = _project(build_synthetic_term_surface())
    atm = next(c for c in result.cells if c.tenor_label == "12m" and c.delta_band == "atm")
    atmp = next(c for c in result.cells if c.tenor_label == "12m" and c.delta_band == "atmp")
    assert -0.55 <= atmp.delta <= -0.45
    assert atmp.gamma == pytest.approx(atm.gamma)
    assert atmp.vega == pytest.approx(atm.vega)


def test_atm_straddle_is_approximately_delta_neutral_and_double_gamma() -> None:
    result = _project(build_synthetic_term_surface())
    atm = next(c for c in result.cells if c.tenor_label == "12m" and c.delta_band == "atm")
    atmp = next(c for c in result.cells if c.tenor_label == "12m" and c.delta_band == "atmp")
    net_dollar_delta = atm.dollar_delta + atmp.dollar_delta
    assert abs(net_dollar_delta) < 0.30 * abs(atm.dollar_delta)
    assert (atm.dollar_gamma + atmp.dollar_gamma) == pytest.approx(2 * atm.dollar_gamma, rel=1e-6)


def test_every_cell_carries_the_second_order_greek_set() -> None:
    result = _project(build_synthetic_term_surface())
    assert result.cells
    for cell in result.cells:
        for field_name in ("vanna", "volga", "charm",
                           "dollar_vanna", "dollar_volga", "dollar_charm"):
            value = getattr(cell, field_name)
            assert value is not None, f"{cell.tenor_label}|{cell.delta_band}.{field_name}"
            assert math.isfinite(value), f"{cell.tenor_label}|{cell.delta_band}.{field_name}"
        assert cell.dollar_vanna_unit == UNIT_STRINGS["dollar_vanna"]
        assert cell.dollar_volga_unit == UNIT_STRINGS["dollar_volga"]
        # ACT/365 monetization (the default) tags charm per calendar day.
        assert cell.dollar_charm_unit == UNIT_STRINGS["dollar_charm_365"]


def _second_order_reprice(
    cell: Any, *, rate: float, volatility: float, maturity_years: float, option_right: str
) -> Any:
    discount_factor = math.exp(-rate * maturity_years)
    state = from_forward(
        forward=cell.forward_price, strike=cell.strike, maturity_years=maturity_years,
        volatility=volatility, discount_factor=discount_factor, option_right=option_right,
        spot=cell.forward_price,
    )
    return price_european(state)


def test_second_order_greeks_match_finite_difference_of_first_order() -> None:
    # Independent check: vanna is ∂delta/∂sigma, volga is ∂vega/∂sigma, charm is -∂delta/∂T.
    # Recompute those sensitivities by central finite difference of the (separately tested)
    # first-order Greeks and compare to the analytic second-order values the projection banked.
    term = build_synthetic_term_surface()
    rate = term.rate
    result = _project(term)

    def cell_at(band: str) -> Any:
        return next(c for c in result.cells if c.tenor_label == "12m" and c.delta_band == band)

    def reprice(cell: Any, right: str, *, vol: float, maturity: float) -> Any:
        return _second_order_reprice(
            cell, rate=rate, volatility=vol, maturity_years=maturity, option_right=right
        )

    h_vol = 1e-5
    h_t = 1e-5

    # vanna and charm on the ATM call (volga vanishes at the money: d1*d2 -> 0).
    atm = cell_at("atm")
    vanna_fd = (
        reprice(atm, "C", vol=atm.implied_vol + h_vol, maturity=atm.maturity_years).delta
        - reprice(atm, "C", vol=atm.implied_vol - h_vol, maturity=atm.maturity_years).delta
    ) / (2 * h_vol)
    d_delta_d_t = (
        reprice(atm, "C", vol=atm.implied_vol, maturity=atm.maturity_years + h_t).delta
        - reprice(atm, "C", vol=atm.implied_vol, maturity=atm.maturity_years - h_t).delta
    ) / (2 * h_t)
    assert atm.vanna == pytest.approx(vanna_fd, rel=1e-4, abs=1e-8)
    assert atm.charm == pytest.approx(-d_delta_d_t, rel=1e-4, abs=1e-8)

    # volga on a put wing, where vega's vol-sensitivity is materially non-zero.
    wing = cell_at("10dp")
    volga_fd = (
        reprice(wing, "P", vol=wing.implied_vol + h_vol, maturity=wing.maturity_years).vega
        - reprice(wing, "P", vol=wing.implied_vol - h_vol, maturity=wing.maturity_years).vega
    ) / (2 * h_vol)
    assert volga_fd != pytest.approx(0.0, abs=1.0)
    assert wing.volga == pytest.approx(volga_fd, rel=1e-4)


def test_dollar_second_order_greeks_follow_the_monetization_rule() -> None:
    # Independent re-derivation of the $-conversion from the raw Greek, matching dollar_greeks:
    # $vanna = vanna*spot*0.01, $volga = volga*0.01*0.01, $charm = charm*spot/365 (ACT/365).
    term = build_synthetic_term_surface()
    result = _project(term)
    spot = term.forward
    wing = next(c for c in result.cells if c.tenor_label == "12m" and c.delta_band == "10dp")
    assert wing.dollar_vanna == pytest.approx(wing.vanna * spot * 0.01, rel=1e-9)
    assert wing.dollar_volga == pytest.approx(wing.volga * 0.01 * 0.01, rel=1e-9)
    assert wing.dollar_charm == pytest.approx(wing.charm * spot / 365.0, rel=1e-9)


def test_out_of_band_target_is_a_labeled_gap_not_a_nan() -> None:
    projection = ProjectionConfig(
        version="v", band_labels=("5dc",), band_targets=(0.05,),
    )
    result = _project(build_synthetic_term_surface(strikes=(95.0, 100.0, 105.0)),
                      projection=projection)
    for c in result.cells:
        assert math.isfinite(c.strike) and math.isfinite(c.implied_vol)
    assert result.gaps
    assert all(g.reason_code in {"delta_out_of_band", "tenor_beyond_span", "no_curve"}
               for g in result.gaps)
    assert any(g.reason_code == "delta_out_of_band" for g in result.gaps)


def test_iv_used_to_price_equals_iv_at_solved_strike() -> None:
    slices = _fit_term_surface(build_synthetic_term_surface())
    result = _project(build_synthetic_term_surface())
    for c in result.cells:
        w = interpolate_total_variance(slices, c.log_moneyness, c.maturity_years)
        expected_iv = math.sqrt(max(w, 0.0) / c.maturity_years)
        assert c.implied_vol == pytest.approx(expected_iv, rel=1e-9)
        assert c.total_variance == pytest.approx(c.implied_vol ** 2 * c.maturity_years, rel=1e-9)


def test_band_axis_is_the_30d_pas2_grid() -> None:
    labels, targets = delta_band_axis(band_low_delta=-0.30, band_high_delta=0.30, band_step=0.02)
    expected_puts = tuple(f"{m:02d}dp" for m in range(30, 1, -2))
    expected_calls = tuple(f"{m:02d}dc" for m in range(2, 31, 2))
    assert labels == expected_puts + ("atm", "atmp") + expected_calls
    assert len(labels) == 32
    assert len(set(labels)) == len(labels)
    assert targets[:15] == tuple(pytest.approx(-m / 100.0) for m in range(30, 1, -2))
    assert targets[15:17] == (0.0, 0.0)
    assert targets[17:] == tuple(pytest.approx(m / 100.0) for m in range(2, 31, 2))


def test_band_axis_rejects_off_grid_or_inverted_bands() -> None:
    with pytest.raises(ProjectionConfigError):
        delta_band_axis(band_low_delta=-0.30, band_high_delta=0.30, band_step=0.025)
    with pytest.raises(ProjectionConfigError):
        delta_band_axis(band_low_delta=-0.301, band_high_delta=0.30, band_step=0.02)
    with pytest.raises(ProjectionConfigError):
        delta_band_axis(band_low_delta=0.10, band_high_delta=0.30, band_step=0.02)


def test_default_projection_offers_the_pas2_grid() -> None:
    expected_labels, _ = delta_band_axis(band_low_delta=-0.30, band_high_delta=0.30, band_step=0.02)
    assert ProjectionConfig(version="v").band_labels == expected_labels
    assert len(expected_labels) == 32
    result = _project(build_synthetic_term_surface())
    bands_12m = {c.delta_band for c in result.cells if c.tenor_label == "12m"}
    assert bands_12m <= set(expected_labels)
    assert {"30dp", "atm", "atmp", "30dc"} <= bands_12m


def test_solved_cells_realize_their_target_delta() -> None:
    term = build_synthetic_term_surface()
    result = _project(term)
    checked = 0
    for c in result.cells:
        if c.delta_band in {"atm", "atmp"}:
            continue
        df = math.exp(-term.rate * c.maturity_years)
        expected_abs = df * abs(c.target_delta)
        assert abs(c.delta) == pytest.approx(expected_abs, rel=1e-4, abs=1e-9), c.delta_band
        assert (c.delta < 0.0) == (c.target_delta < 0.0)
        checked += 1
    assert checked >= 30


def test_strikes_are_monotone_in_target_nd1() -> None:
    result = _project(build_synthetic_term_surface())
    cells_12m = [c for c in result.cells if c.tenor_label == "12m"]

    def target_nd1(t: float) -> float:
        return 0.5 if t == 0.0 else (t if t > 0.0 else 1.0 + t)

    ordered = sorted(cells_12m, key=lambda c: target_nd1(c.target_delta), reverse=True)
    strikes = [c.strike for c in ordered]
    assert strikes == sorted(strikes)


def test_step2_deep_otm_extremes_are_labeled_gaps_not_nans() -> None:
    result = _project(build_synthetic_term_surface(strikes=(95.0, 100.0, 105.0)))
    for c in result.cells:
        assert math.isfinite(c.strike) and math.isfinite(c.implied_vol)
    deep_gaps = {g.delta_band for g in result.gaps if g.reason_code == "delta_out_of_band"}
    assert {"02dp", "02dc"} & deep_gaps
    assert all(
        g.reason_code in {"delta_out_of_band", "tenor_beyond_span", "no_curve"}
        for g in result.gaps
    )


def test_dollar_greeks_match_hand_values() -> None:
    result = _project(build_synthetic_term_surface())
    cell = result.cells[len(result.cells) // 2]
    s = cell.forward_price
    assert cell.dollar_delta == pytest.approx(cell.delta * s, rel=1e-12)
    assert cell.dollar_gamma == pytest.approx(cell.gamma * s * s / 100.0, rel=1e-12)
    assert cell.dollar_vega == pytest.approx(cell.vega * 0.01, rel=1e-12)
    assert cell.dollar_theta == pytest.approx(cell.theta / 365.0, rel=1e-12)
    assert cell.dollar_rho == pytest.approx(cell.rho * 0.01, rel=1e-12)


def test_dollar_greeks_match_standalone_dollar_greeks_engine() -> None:
    mon = MonetizationConfig(version="mon-test")
    result = _project(build_synthetic_term_surface(), monetization=mon)
    cell = result.cells[0]
    direct = dollar_greeks(
        delta=cell.delta, gamma=cell.gamma, vega=cell.vega, theta=cell.theta, rho=cell.rho,
        spot=cell.forward_price, multiplier=1.0, quantity=1.0, config=mon,
    )
    assert cell.dollar_delta == direct.dollar_delta
    assert cell.dollar_gamma == direct.dollar_gamma
    assert cell.dollar_vega == direct.dollar_vega
    assert cell.dollar_theta == direct.dollar_theta
    assert cell.dollar_rho == direct.dollar_rho


def test_gamma_flag_1pct_vs_dollar() -> None:
    term = build_synthetic_term_surface()
    pct = _project(term, monetization=MonetizationConfig(
        version="m", gamma_normalisation="one_pct"))
    dol = _project(term, monetization=MonetizationConfig(
        version="m", gamma_normalisation="one_dollar"))
    a = pct.cells[0]
    b = next(c for c in dol.cells if c.tenor_label == a.tenor_label and c.delta_band == a.delta_band)
    assert b.dollar_gamma == pytest.approx(a.dollar_gamma * 100.0, rel=1e-12)
    assert b.dollar_delta == pytest.approx(a.dollar_delta, rel=1e-12)
    assert b.dollar_vega == pytest.approx(a.dollar_vega, rel=1e-12)
    assert b.dollar_theta == pytest.approx(a.dollar_theta, rel=1e-12)
    assert b.dollar_gamma_unit == UNIT_STRINGS["dollar_gamma_one_dollar"]
    assert a.dollar_gamma_unit == UNIT_STRINGS["dollar_gamma_one_pct"]


def test_theta_flag_365_vs_252() -> None:
    term = build_synthetic_term_surface()
    cal = _project(term, monetization=MonetizationConfig(version="m", theta_day_count=365))
    trd = _project(term, monetization=MonetizationConfig(version="m", theta_day_count=252))
    a = cal.cells[0]
    b = next(c for c in trd.cells if c.tenor_label == a.tenor_label and c.delta_band == a.delta_band)
    assert a.dollar_theta is not None and b.dollar_theta is not None
    assert b.dollar_theta == pytest.approx(a.dollar_theta * 365.0 / 252.0, rel=1e-12)
    assert b.dollar_delta == pytest.approx(a.dollar_delta, rel=1e-12)
    assert b.dollar_gamma == pytest.approx(a.dollar_gamma, rel=1e-12)
    assert b.dollar_theta_unit == UNIT_STRINGS["dollar_theta_252"]
    assert a.dollar_theta_unit == UNIT_STRINGS["dollar_theta_365"]


def test_dollar_greeks_carry_unit_strings() -> None:
    cell = _project(build_synthetic_term_surface()).cells[0]
    assert cell.dollar_delta_unit == UNIT_STRINGS["dollar_delta"]
    assert cell.dollar_gamma_unit == UNIT_STRINGS["dollar_gamma_one_pct"]
    assert cell.dollar_vega_unit == UNIT_STRINGS["dollar_vega"]
    assert cell.dollar_theta_unit == UNIT_STRINGS["dollar_theta_365"]
    assert cell.dollar_rho_unit == UNIT_STRINGS["dollar_rho"]
    assert cell.dollar_rt_vega_unit == UNIT_STRINGS["dollar_rt_vega"]
    assert cell.rt_vega == pytest.approx(cell.vega / math.sqrt(cell.maturity_years), rel=1e-12)
    assert cell.dollar_rt_vega == pytest.approx(cell.rt_vega * 0.01, rel=1e-12)
    for name in ("delta", "gamma", "vega", "rt_vega", "theta", "rho"):
        assert math.isfinite(getattr(cell, name))


@settings(max_examples=40, deadline=None)
@given(
    a_per_year=st.floats(min_value=0.01, max_value=0.10),
    b=st.floats(min_value=0.02, max_value=0.12),
    rho=st.floats(min_value=-0.6, max_value=0.6),
    sigma=st.floats(min_value=0.1, max_value=0.5),
    k=st.floats(min_value=-0.4, max_value=0.4),
)
def test_tenor_interpolation_is_calendar_no_arb(
    a_per_year: float, b: float, rho: float, sigma: float, k: float
) -> None:
    term = build_synthetic_term_surface(
        svi_a_per_year=a_per_year, svi_b=b, svi_rho=rho, svi_sigma=sigma,
    )
    slices = _fit_term_surface(term)
    maturities = [tenor_years(t) for t in PINNED_TENORS]
    previous = -math.inf
    for maturity in maturities:
        w = interpolate_total_variance(slices, k, maturity)
        assert w >= previous - 1e-9
        previous = w


def test_regrid_matches_the_generator_oracle() -> None:
    term = build_synthetic_term_surface()
    slices = _fit_term_surface(term)
    for k in (-0.2, 0.0, 0.2):
        for maturity in (0.5, 1.0, 2.0):
            got = interpolate_total_variance(slices, k, maturity)
            expected = term.true_total_variance(k, maturity)
            assert got == pytest.approx(expected, abs=2e-3)


def test_no_lookahead_in_projection() -> None:
    term = build_synthetic_term_surface()
    base = _project(term, snapshot_ts=TS)
    later = _project(term, snapshot_ts=LATER_TS)
    base_by_key = {(c.tenor_label, c.delta_band): c for c in base.cells}
    for c in later.cells:
        b = base_by_key[(c.tenor_label, c.delta_band)]
        assert c.strike == pytest.approx(b.strike, rel=1e-12)
        assert c.implied_vol == pytest.approx(b.implied_vol, rel=1e-12)
        assert c.price == pytest.approx(b.price, rel=1e-12)
        assert c.delta == pytest.approx(b.delta, rel=1e-12)


def test_reordering_invariance() -> None:
    term = build_synthetic_term_surface()
    slices = _fit_term_surface(term)
    market = _market(term)
    a = project_grid(
        slices, market, snapshot_ts=TS, source_snapshot_ts=TS, calc_ts=TS,
        projection=ProjectionConfig(version="p"), monetization=MonetizationConfig(version="m"),
        config_hashes=CONFIG_HASHES,
    )
    shuffled = tuple(reversed(slices))
    b = project_grid(
        shuffled, market, snapshot_ts=TS, source_snapshot_ts=TS, calc_ts=TS,
        projection=ProjectionConfig(version="p"), monetization=MonetizationConfig(version="m"),
        config_hashes=CONFIG_HASHES,
    )
    assert [(c.tenor_label, c.delta_band) for c in a.cells] == \
           [(c.tenor_label, c.delta_band) for c in b.cells]
    for ca, cb in zip(a.cells, b.cells, strict=True):
        assert ca == cb
    assert {c.provenance.stamp_hash for c in a.cells} == {c.provenance.stamp_hash for c in b.cells}


def test_empty_chain_yields_all_gaps_no_cells() -> None:
    market = SnapshotMarketState(underlying="AAPL", provider="DERIBIT", spot=100.0)
    result = project_grid(
        (), market, snapshot_ts=TS, source_snapshot_ts=TS, calc_ts=TS,
        projection=ProjectionConfig(version="p"), monetization=MonetizationConfig(version="m"),
        config_hashes=CONFIG_HASHES,
    )
    assert result.cells == ()
    assert result.gaps
    assert all(g.reason_code == "no_curve" for g in result.gaps)


def test_single_expiry_cannot_span_the_tenor_grid() -> None:
    term = build_synthetic_term_surface(maturities=(1.0,))
    result = _project(term)
    produced_tenors = {c.tenor_label for c in result.cells}
    assert "12m" in produced_tenors
    assert any(g.tenor_label == "3y" and g.reason_code == "tenor_beyond_span" for g in result.gaps)
    assert any(g.tenor_label == "10d" and g.reason_code == "tenor_beyond_span" for g in result.gaps)


def test_tenor_beyond_span_is_a_labeled_gap() -> None:
    term = build_synthetic_term_surface(maturities=(10.0 / 365.0, 0.5, 1.0))
    result = _project(term)
    long_gaps = {g.tenor_label for g in result.gaps if g.reason_code == "tenor_beyond_span"}
    assert {"18m", "2y", "3y"} <= long_gaps
    assert not any(c.tenor_label in {"18m", "2y", "3y"} for c in result.cells)


def test_strike_exactly_at_band_edge_is_kept() -> None:
    result = _project(build_synthetic_term_surface())
    edges = {(c.tenor_label, c.delta_band) for c in result.cells}
    assert ("12m", "30dp") in edges
    assert ("12m", "30dc") in edges


def test_band_targets_out_of_range_rejected() -> None:
    with pytest.raises(ProjectionConfigError):
        ProjectionConfig(version="v", band_labels=("x",), band_targets=(1.5,))
    with pytest.raises(ProjectionConfigError):
        ProjectionConfig(version="v", band_labels=("x",), band_targets=(float("nan"),))


def test_mismatched_band_axis_lengths_rejected() -> None:
    with pytest.raises(ProjectionConfigError):
        ProjectionConfig(version="v", band_labels=("a", "b"), band_targets=(0.1,))


def test_unknown_tenor_label_raises() -> None:
    with pytest.raises(ProjectionConfigError):
        tenor_years("9m")


def test_projection_config_rejects_empty_version_and_bands() -> None:
    with pytest.raises(ProjectionConfigError):
        ProjectionConfig(version="")
    with pytest.raises(ProjectionConfigError):
        ProjectionConfig(version="v", band_labels=(), band_targets=())
    with pytest.raises(ProjectionConfigError):
        ProjectionConfig(version="v", band_labels=("a", "a"), band_targets=(0.1, 0.2))
    with pytest.raises(ProjectionConfigError):
        ProjectionConfig(version="v", interpolation="cubic")


def test_insufficient_slice_mixed_with_a_curve_is_skipped_not_crashed() -> None:
    term = build_synthetic_term_surface()
    slices = list(_fit_term_surface(term))
    empty_slice = fit_slice("AAPL", 5.0, (), expiry_date=EXPIRY, day_count="ACT/365",
                            config=SURFACE_CONFIG)
    slices.append(empty_slice)
    result = project_grid(
        slices, _market(term), snapshot_ts=TS, source_snapshot_ts=TS, calc_ts=TS,
        projection=ProjectionConfig(version="p"), monetization=MonetizationConfig(version="m"),
        config_hashes=CONFIG_HASHES,
    )
    assert result.cells
    assert empty_slice.method == "insufficient"


def test_solver_returns_none_when_target_unbracketed() -> None:
    from algotrading.infra.surfaces.projection import _solve_strike_for_delta
    slices = _fit_term_surface(build_synthetic_term_surface(strikes=(95.0, 100.0, 105.0)))
    k = _solve_strike_for_delta(
        slices, target_delta=0.01, forward=100.0, maturity_years=1.0,
        discount_factor=0.98, span=(math.log(0.95), math.log(1.05)),
    )
    assert k is None


def test_solver_returns_a_strike_inside_the_span_for_an_in_band_target() -> None:
    from algotrading.infra.surfaces.projection import _solve_strike_for_delta
    term = build_synthetic_term_surface()
    slices = _fit_term_surface(term)
    span = (math.log(0.6), math.log(1.4))
    k = _solve_strike_for_delta(
        slices, target_delta=0.0, forward=100.0, maturity_years=1.0,
        discount_factor=math.exp(-0.02), span=span,
    )
    assert k is not None
    assert span[0] <= k <= span[1]


def _listed_expiry_market(term: SyntheticTermSurface) -> SnapshotMarketState:
    return SnapshotMarketState(
        underlying="AAPL", provider="DERIBIT", spot=term.forward,
        discount_factors={
            round(t, 9): math.exp(-term.rate * t) for t in term.maturities
        },
        default_discount_factor=1.0,
        spot_is_forward=True,
    )


def test_discount_factor_exact_key_hit_returns_the_stored_value() -> None:
    market = SnapshotMarketState(
        underlying="AAPL", provider="DERIBIT", spot=100.0,
        discount_factors={0.5: 0.99123456789, 1.0: 0.97},
    )
    assert market.discount_factor_at(0.5) == 0.99123456789
    assert market.discount_factor_at(1.0) == 0.97


def test_discount_factor_flat_rate_listed_curve_recovers_the_rate_at_every_tenor() -> None:
    term = build_synthetic_term_surface()
    market = _listed_expiry_market(term)
    for label in PINNED_TENORS:
        maturity = tenor_years(label)
        assert market.discount_factor_at(maturity) == pytest.approx(
            math.exp(-term.rate * maturity), rel=1e-9
        ), label


def test_discount_factor_interpolates_log_linearly_between_knots() -> None:
    market = SnapshotMarketState(
        underlying="AAPL", provider="DERIBIT", spot=100.0,
        discount_factors={1.0: 0.98, 2.0: 0.94},
    )
    y1, y2 = -math.log(0.98), -math.log(0.94)
    expected = math.exp(-(y1 + 0.25 * (y2 - y1)))
    assert market.discount_factor_at(1.25) == pytest.approx(expected, rel=1e-12)


def test_discount_factor_extrapolates_flat_zero_rate_beyond_the_knot_span() -> None:
    market = SnapshotMarketState(
        underlying="AAPL", provider="DERIBIT", spot=100.0,
        discount_factors={1.0: 0.98, 2.0: 0.94},
    )
    r_short = -math.log(0.98) / 1.0
    r_long = -math.log(0.94) / 2.0
    assert market.discount_factor_at(0.25) == pytest.approx(math.exp(-r_short * 0.25), rel=1e-12)
    assert market.discount_factor_at(3.0) == pytest.approx(math.exp(-r_long * 3.0), rel=1e-12)


def test_discount_factor_single_knot_curve_holds_its_zero_rate_flat() -> None:
    market = SnapshotMarketState(
        underlying="AAPL", provider="DERIBIT", spot=100.0,
        discount_factors={0.5: math.exp(-0.03 * 0.5)},
    )
    assert market.discount_factor_at(1.0) == pytest.approx(math.exp(-0.03), rel=1e-12)


def test_discount_factor_tenor_label_binding_wins_over_the_curve() -> None:
    market = SnapshotMarketState(
        underlying="AAPL", provider="DERIBIT", spot=100.0,
        discount_factors={1.0: 0.98, 2.0: 0.94},
        discount_factors_by_tenor={"18m": 0.9123},
    )
    assert market.discount_factor_for("18m", tenor_years("18m")) == 0.9123
    assert market.discount_factor_for("12m", 1.0) == 0.98


def test_discount_factor_empty_curve_falls_back_to_the_default() -> None:
    market = SnapshotMarketState(
        underlying="AAPL", provider="DERIBIT", spot=100.0, default_discount_factor=0.97,
    )
    assert market.discount_factor_at(1.0) == 0.97


def test_projection_prices_with_the_listed_expiry_curve_not_rate_free() -> None:
    term = build_synthetic_term_surface()
    slices = _fit_term_surface(term)
    kwargs: dict[str, Any] = dict(
        snapshot_ts=TS, source_snapshot_ts=TS, calc_ts=TS,
        projection=ProjectionConfig(version="proj-test"),
        monetization=MonetizationConfig(version="mon-test"),
        config_hashes=CONFIG_HASHES,
    )
    listed = project_grid(slices, _listed_expiry_market(term), **kwargs)
    pinned = project_grid(slices, _market(term), **kwargs)
    assert listed.cells and len(listed.cells) == len(pinned.cells)
    rate_free = project_grid(
        slices,
        SnapshotMarketState(
            underlying="AAPL", provider="DERIBIT", spot=term.forward, discount_factors={},
            spot_is_forward=True,
        ),
        **kwargs,
    )
    for got, want in zip(listed.cells, pinned.cells, strict=True):
        assert got.price == pytest.approx(want.price, rel=1e-9)
        assert got.delta == pytest.approx(want.delta, rel=1e-9)
        assert got.rho == pytest.approx(want.rho, rel=1e-9)
    atm_3y = next(c for c in listed.cells if c.tenor_label == "3y" and c.delta_band == "atm")
    atm_3y_free = next(
        c for c in rate_free.cells if c.tenor_label == "3y" and c.delta_band == "atm"
    )
    assert atm_3y.price < atm_3y_free.price


def test_projected_cell_round_trips_through_storage(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    cell = _project(build_synthetic_term_surface()).cells[0]
    store.write("projected_option_analytics", [cell])
    back = store.read(
        "projected_option_analytics", trade_date=TS.date(),
        underlying=cell.underlying, provider=cell.provider,
    )
    assert len(back) == 1
    assert back[0] == cell


def test_full_grid_round_trips_through_storage(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    result = _project(build_synthetic_term_surface())
    store.write("projected_option_analytics", list(result.cells))
    back = store.read("projected_option_analytics", provider="DERIBIT")
    assert len(back) == len(result.cells)
    assert {(c.tenor_label, c.delta_band) for c in back} == \
           {(c.tenor_label, c.delta_band) for c in result.cells}


def test_malformed_cell_is_rejected_by_write_ahead_validation(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    good = _project(build_synthetic_term_surface()).cells[0]
    bad = dataclasses.replace(good, implied_vol=float("nan"))
    with pytest.raises(ContractValidationError):
        store.write("projected_option_analytics", [bad])
    bad_strike = dataclasses.replace(good, strike=-1.0)
    with pytest.raises(ContractValidationError):
        store.write("projected_option_analytics", [bad_strike])


def test_two_providers_land_in_disjoint_partitions(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    deribit = _project(build_synthetic_term_surface(), provider="DERIBIT").cells[0]
    ibkr = dataclasses.replace(deribit, provider="IBKR", price=deribit.price + 1.0)
    store.write("projected_option_analytics", [deribit])
    store.write("projected_option_analytics", [ibkr])
    both = store.read("projected_option_analytics")
    assert {c.provider for c in both} == {"DERIBIT", "IBKR"}
    only_ibkr = store.read("projected_option_analytics", provider="IBKR")
    assert [c.provider for c in only_ibkr] == ["IBKR"]


def test_table_is_provider_partitioned_in_registry() -> None:
    spec = spec_for_table("projected_option_analytics")
    assert spec.provider_partitioned is True
    assert spec.layer == "analytics"


def compute_grid_summary() -> dict[str, Any]:
    result = _project(build_synthetic_term_surface())
    cells = {
        f"{c.tenor_label}|{c.delta_band}": {
            "strike": c.strike, "implied_vol": c.implied_vol, "price": c.price,
            "delta": c.delta, "gamma": c.gamma, "vega": c.vega, "theta": c.theta, "rho": c.rho,
            "dollar_delta": c.dollar_delta, "dollar_gamma": c.dollar_gamma,
            "dollar_theta": c.dollar_theta,
            "stamp_hash": c.provenance.stamp_hash,
        }
        for c in result.cells
    }
    return {
        "cells": cells,
        "gap_count": len(result.gaps),
        "config_hash_keys": sorted(result.cells[0].provenance.config_hashes),
    }


def test_projection_golden_byte_identical(golden_artifact: Any) -> None:
    summary = compute_grid_summary()
    golden = golden_artifact(_GOLDEN_PATH, summary)
    assert summary["gap_count"] == golden["gap_count"]
    assert summary["config_hash_keys"] == golden["config_hash_keys"]
    assert set(summary["cells"]) == set(golden["cells"])
    for key, cell in summary["cells"].items():
        g = golden["cells"][key]
        assert cell["stamp_hash"] == g["stamp_hash"], key
        for field_name in ("strike", "implied_vol", "price", "delta", "gamma", "vega",
                           "theta", "rho", "dollar_delta", "dollar_gamma", "dollar_theta"):
            assert cell[field_name] == pytest.approx(g[field_name], rel=1e-9, abs=1e-12), \
                f"{key}.{field_name}"


_SUBPROCESS_SCRIPT = """
import json
from test_analytics_projection import compute_grid_summary
print(json.dumps(compute_grid_summary()))
"""


def test_grid_stamp_hash_is_stable_across_processes() -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = _TESTS_DIR
    env.pop("PYTHONHASHSEED", None)
    completed = subprocess.run(
        [sys.executable, "-c", _SUBPROCESS_SCRIPT],
        capture_output=True, text=True, env=env, check=True,
    )
    other = json.loads(completed.stdout)
    here = compute_grid_summary()
    assert set(other["cells"]) == set(here["cells"])
    for key, cell in here["cells"].items():
        assert other["cells"][key]["stamp_hash"] == cell["stamp_hash"], key


def test_projection_config_hash_is_stable_across_processes() -> None:
    script = (
        "from algotrading.infra.surfaces.projection import ProjectionConfig;"
        "print(ProjectionConfig(version='proj-test').config_hash())"
    )
    env = dict(os.environ)
    env.pop("PYTHONHASHSEED", None)
    out = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, env=env, check=True,
    ).stdout.strip()
    assert out == ProjectionConfig(version="proj-test").config_hash()
    minus_zero = ProjectionConfig(version="proj-test", band_labels=("atm",), band_targets=(-0.0,))
    plus_zero = ProjectionConfig(version="proj-test", band_labels=("atm",), band_targets=(0.0,))
    assert minus_zero.config_hash() == plus_zero.config_hash()
    assert "NaN" not in canonical_json(ProjectionConfig(version="proj-test"))


def test_projection_config_hashes_match_the_pinned_golden_digests() -> None:
    pinned = ProjectionConfig(version="proj-pin-1")
    assert pinned.config_hash() == (
        "147281d6ac424124a216d0e3901dc1cf58ab72aef38999112ace46362ffd6205"
    )
    merged = merged_config_hashes(
        {"universe": "u"},
        projection=pinned,
        monetization=MonetizationConfig(version="mon-pin-1"),
    )
    assert merged == {
        "universe": "u",
        "projection": "147281d6ac424124a216d0e3901dc1cf58ab72aef38999112ace46362ffd6205",
        "scenarios": "7fc8935ae8ddc4be16c0fabaaedc1ebde6e7baa260a0583e994d36d3bf4a1327",
    }


def test_pricer_version_has_one_home_in_the_pricing_engine() -> None:
    from algotrading.infra.pricing import PRICER_VERSION as engine_version
    from algotrading.infra.surfaces import projection as projection_module

    assert projection_module.PRICER_VERSION is engine_version
    assert engine_version == "black76-lr-1.0.0"


def test_second_order_greeks_are_populated_with_units_on_every_cell() -> None:
    result = _project(build_synthetic_term_surface())
    assert result.cells
    for cell in result.cells:
        for field_name in ("vanna", "volga", "charm",
                            "dollar_vanna", "dollar_volga", "dollar_charm"):
            value = getattr(cell, field_name)
            assert value is not None and math.isfinite(value), f"{field_name} dropped on a cell"
        assert cell.dollar_vanna_unit == UNIT_STRINGS["dollar_vanna"]
        assert cell.dollar_volga_unit == UNIT_STRINGS["dollar_volga"]
        assert cell.dollar_charm_unit == UNIT_STRINGS["dollar_charm_365"]


def test_dollar_second_order_greeks_match_the_monetization_formulas() -> None:
    cell = _project(build_synthetic_term_surface()).cells[0]
    s = cell.forward_price
    assert cell.vanna is not None and cell.volga is not None and cell.charm is not None
    assert cell.dollar_vanna == pytest.approx(cell.vanna * s * 0.01, rel=1e-12)
    assert cell.dollar_volga == pytest.approx(cell.volga * 0.01 * 0.01, rel=1e-12)
    assert cell.dollar_charm == pytest.approx(cell.charm * s / 365.0, rel=1e-12)


def _reprice(
    *, forward: float, strike: float, vol: float, rate: float, maturity: float, right: str
) -> Any:
    from algotrading.infra.pricing import from_forward, price_european

    return price_european(
        from_forward(
            forward=forward, strike=strike, maturity_years=maturity, volatility=vol,
            discount_factor=math.exp(-rate * maturity), option_right=right, spot=forward,
        )
    )


def test_second_order_greeks_match_finite_difference_of_the_public_pricer() -> None:
    term = build_synthetic_term_surface()
    result = _project(term)
    cell = next(c for c in result.cells if c.tenor_label == "12m" and c.delta_band == "30dc")

    fwd, strike, sigma, maturity = (
        cell.forward_price, cell.strike, cell.implied_vol, cell.maturity_years
    )
    rate = term.rate
    right = "C"

    d_sigma = 1e-5
    up_sigma = _reprice(forward=fwd, strike=strike, vol=sigma + d_sigma, rate=rate,
                        maturity=maturity, right=right)
    dn_sigma = _reprice(forward=fwd, strike=strike, vol=sigma - d_sigma, rate=rate,
                        maturity=maturity, right=right)
    fd_vanna = (up_sigma.delta - dn_sigma.delta) / (2.0 * d_sigma)
    fd_volga = (up_sigma.vega - dn_sigma.vega) / (2.0 * d_sigma)

    d_t = 1e-6
    up_t = _reprice(forward=fwd, strike=strike, vol=sigma, rate=rate,
                    maturity=maturity + d_t, right=right)
    dn_t = _reprice(forward=fwd, strike=strike, vol=sigma, rate=rate,
                    maturity=maturity - d_t, right=right)
    fd_charm = -(up_t.delta - dn_t.delta) / (2.0 * d_t)

    assert cell.vanna == pytest.approx(fd_vanna, rel=2e-5, abs=1e-9), (
        "projected vanna must equal dDelta/dSigma by central difference, not "
        "price_european(...).vanna (that comparison would be circular)"
    )
    assert cell.volga == pytest.approx(fd_volga, rel=2e-5, abs=1e-9), (
        "projected volga must equal dVega/dSigma by central difference"
    )
    assert cell.charm == pytest.approx(fd_charm, rel=2e-4, abs=1e-9), (
        "projected charm must equal -dDelta/dMaturity (decay as calendar time passes) "
        "by central difference, holding the implied rate fixed as the analytic charm does"
    )


# ---------------------------------------------------------------------------
# D1 / D4 / D5: listed-contract rows, captured forward curve, honest atmf label,
# and the canonical band->right resolver. (Reconciled: the forward is sourced
# from market.forward_for, not the dropped resolve_forward/forwards= param.)
# ---------------------------------------------------------------------------

from algotrading.infra.surfaces.projection import (  # noqa: E402
    ListedContract,
    option_right_for_band,
)


def _listed_market(
    term: SyntheticTermSurface,
    *,
    forwards: dict[float, float] | None,
    underlying: str = "AAPL",
    provider: str = "DERIBIT",
) -> SnapshotMarketState:
    """A market state carrying the captured forward curve (D4).

    `forwards` maps maturity-years -> forward. When None the caller opts out of the curve and
    declares spot==forward (legacy/synthetic), matching the old resolve_forward(None) fallback.
    """
    return SnapshotMarketState(
        underlying=underlying,
        provider=provider,
        spot=term.forward,
        discount_factors={t: math.exp(-term.rate * t) for t in PINNED_TENORS_YEARS()},
        default_discount_factor=1.0,
        forwards=dict(forwards) if forwards is not None else {},
        spot_is_forward=forwards is None,
    )


def _project_listed(
    term: SyntheticTermSurface,
    contracts: tuple[ListedContract, ...],
    *,
    forwards: dict[float, float] | None = None,
) -> Any:
    slices = _fit_term_surface(term)
    return project_grid(
        slices, _listed_market(term, forwards=forwards),
        snapshot_ts=TS, source_snapshot_ts=TS, calc_ts=TS,
        projection=ProjectionConfig(version="proj-test"),
        monetization=MonetizationConfig(version="mon-test"),
        config_hashes=CONFIG_HASHES,
        listed_contracts=contracts,
    )


def test_resolver_maps_every_band_label_to_a_right() -> None:
    # The canonical resolver the BFF imports: atm->C, atmp->P, suffix ...c->C, ...p->P.
    assert option_right_for_band("atm") == "C"
    assert option_right_for_band("atmf") == "C"  # at-the-money-forward call leg
    assert option_right_for_band("atmp") == "P"
    assert option_right_for_band("30dc") == "C"
    assert option_right_for_band("30dp") == "P"
    assert option_right_for_band("02dc") == "C"


def test_resolver_rejects_an_unparseable_label() -> None:
    with pytest.raises(ProjectionConfigError):
        option_right_for_band("garbage")


def test_market_forward_for_uses_the_curve_then_flags_a_missing_maturity() -> None:
    # Reconciled D4: the per-maturity forward is the captured curve value (interpolated in
    # log-space), not spot. A maturity below/inside the curve resolves; with no curve at all
    # and spot_is_forward, spot stands in. This is the carrier the projection now reads.
    market = SnapshotMarketState(
        underlying="AAPL", provider="DERIBIT", spot=100.0,
        forwards={1.0: 105.5, 2.0: 108.0},
    )
    assert market.forward_for("12m", 1.0) == 105.5
    assert market.forward_for("2y", 2.0) == 108.0
    # Inside the knots, log-linear interpolation lands strictly between them.
    mid = market.forward_for("18m", 1.5)
    assert mid is not None and 105.5 < mid < 108.0
    # No curve and spot not declared a forward -> no anchor (honest miss, never a spot guess).
    bare = SnapshotMarketState(underlying="AAPL", provider="DERIBIT", spot=100.0)
    assert bare.forward_for("12m", 1.0) is None
    # No curve but spot IS the forward (legacy/synthetic) -> spot stands in.
    legacy = SnapshotMarketState(
        underlying="AAPL", provider="DERIBIT", spot=100.0, spot_is_forward=True
    )
    assert legacy.forward_for("12m", 1.0) == 100.0


def test_listed_grid_emits_one_row_per_listed_contract_at_the_listed_strike() -> None:
    term = build_synthetic_term_surface()  # forward == spot == 100.0, fitted at T=1.0 (12m)
    contracts = (
        ListedContract(tenor_label="12m", maturity_years=1.0, right="C", strike=95.0),
        ListedContract(tenor_label="12m", maturity_years=1.0, right="P", strike=95.0),
        ListedContract(tenor_label="12m", maturity_years=1.0, right="C", strike=105.0),
    )
    result = _project_listed(term, contracts, forwards={1.0: 100.0})
    # One combined-side cell per listed contract (no per-side wings supplied here).
    assert len(result.cells) == 3
    # The (strike, right) keys round-trip exactly: rows ARE the listed contracts.
    emitted_keys = {
        (c.strike, "C" if c.delta >= 0.0 else "P") for c in result.cells
    }
    assert emitted_keys == {(95.0, "C"), (95.0, "P"), (105.0, "C")}
    # Each cell sits at exactly the LISTED strike, and log-moneyness is against the forward.
    for c in result.cells:
        assert c.strike in (95.0, 105.0)
        assert c.log_moneyness == pytest.approx(math.log(c.strike / c.forward_price), rel=1e-12)


def test_listed_row_price_matches_independent_black_at_the_listed_strike() -> None:
    # Independent oracle: price the listed strike with Black-76 off the FITTED iv and the
    # captured forward, derived here without calling project_grid's pricer path.
    term = build_synthetic_term_surface()
    forward = 100.0  # equals the fitted forward, so log-moneyness lines up with the fit
    rate = term.rate
    maturity = 1.0
    strike = 110.0
    df = math.exp(-rate * maturity)
    slices = _fit_term_surface(term)
    k = math.log(strike / forward)
    expected_iv = math.sqrt(max(interpolate_total_variance(slices, k, maturity), 0.0) / maturity)
    expected = price_european(
        from_forward(
            forward=forward, strike=strike, maturity_years=maturity, volatility=expected_iv,
            discount_factor=df, option_right="C", spot=forward,
        )
    )
    result = _project_listed(
        term,
        (ListedContract(tenor_label="12m", maturity_years=1.0, right="C", strike=strike),),
        forwards={1.0: forward},
    )
    cell = next(c for c in result.cells if c.surface_side == "combined")
    assert cell.implied_vol == pytest.approx(expected_iv, rel=1e-12)
    assert cell.price == pytest.approx(expected.price, rel=1e-12)
    assert cell.delta == pytest.approx(expected.delta, rel=1e-12)
    assert cell.forward_price == pytest.approx(forward, rel=1e-12)


def test_listed_grid_uses_the_captured_forward_not_spot() -> None:
    # D4: spot is 100, but the captured 12m forward is 105. The atm-forward strike must
    # follow the captured forward, so a strike of 105 (= forward) is the at-the-money-forward
    # row and a strike of 100 (= spot) is now in-the-money for a call (delta > 0.5).
    term = build_synthetic_term_surface()  # spot == 100
    contracts = (
        ListedContract(tenor_label="12m", maturity_years=1.0, right="C", strike=105.0),
        ListedContract(tenor_label="12m", maturity_years=1.0, right="C", strike=100.0),
    )
    result = _project_listed(term, contracts, forwards={1.0: 105.0})
    at_fwd = next(c for c in result.cells if c.strike == 105.0)
    at_spot = next(c for c in result.cells if c.strike == 100.0)
    assert at_fwd.forward_price == pytest.approx(105.0)
    # k=0 at the forward -> N(d1) ~ 0.5 -> honest at-the-money-forward label (D5).
    assert at_fwd.log_moneyness == pytest.approx(0.0, abs=1e-12)
    assert at_fwd.delta_band == "atmf"
    # the spot strike (below the forward) is a call wing above 0.5 delta, not labeled atm.
    assert at_spot.delta_band != "atmf"
    assert at_spot.delta > at_fwd.delta


def test_listed_grid_flags_a_maturity_with_no_captured_forward() -> None:
    # D4 honesty: when no forward can be anchored (empty curve, spot not declared a forward),
    # every listed contract is a labeled no_forward gap, never a silent spot substitution.
    term = build_synthetic_term_surface()
    contracts = (
        ListedContract(tenor_label="12m", maturity_years=1.0, right="C", strike=100.0),
        ListedContract(tenor_label="2y", maturity_years=2.0, right="C", strike=100.0),
    )
    # forwards=None alone would let spot stand in (spot_is_forward); to exercise a genuine miss
    # we hand the projection a market with no curve and spot NOT declared a forward.
    slices = _fit_term_surface(term)
    bare = SnapshotMarketState(
        underlying="AAPL", provider="DERIBIT", spot=term.forward,
        discount_factors={t: math.exp(-term.rate * t) for t in PINNED_TENORS_YEARS()},
        default_discount_factor=1.0,
    )
    result = project_grid(
        slices, bare,
        snapshot_ts=TS, source_snapshot_ts=TS, calc_ts=TS,
        projection=ProjectionConfig(version="proj-test"),
        monetization=MonetizationConfig(version="mon-test"),
        config_hashes=CONFIG_HASHES,
        listed_contracts=contracts,
    )
    assert not result.cells
    no_fwd = [g for g in result.gaps if g.reason_code == "no_forward"]
    assert {g.tenor_label for g in no_fwd} == {"12m", "2y"}


def test_listed_band_label_groups_by_model_delta() -> None:
    # The display band is derived from the MODEL delta at the listed strike (for grouping),
    # keeping the existing "..dc"/"..dp" vocabulary. A deep call wing -> "..dc"; a put -> "..dp".
    term = build_synthetic_term_surface()
    contracts = (
        ListedContract(tenor_label="12m", maturity_years=1.0, right="C", strike=130.0),
        ListedContract(tenor_label="12m", maturity_years=1.0, right="P", strike=70.0),
    )
    result = _project_listed(term, contracts, forwards={1.0: 100.0})
    deep_call = next(c for c in result.cells if c.strike == 130.0)
    deep_put = next(c for c in result.cells if c.strike == 70.0)
    # High strike -> low call delta -> small "..dc" magnitude.
    assert deep_call.delta_band.endswith("dc")
    assert deep_put.delta_band.endswith("dp")


def test_listed_grid_legacy_callers_still_get_the_delta_band_grid() -> None:
    # Passing no listed_contracts keeps the original delta-band emission untouched.
    result = _project(build_synthetic_term_surface())
    assert {"30dp", "atm", "atmp", "30dc"} <= {
        c.delta_band for c in result.cells if c.tenor_label == "12m"
    }
