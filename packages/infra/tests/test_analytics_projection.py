"""WS 1F — the tenor × delta-band analytics projection, every named case.

Read ``tasks/TESTING.md`` and ``tasks/1F-analytics-projection.md``. The independent
oracles here are the synthetic term-surface generator (``fixtures.synthetic`` — the true
SVI total variance at any ``(k, T)``, calendar-consistent by construction) and the
hand-computed $-Greek formulas in the test comments — never the projection code itself.

Cases (the spec's Test surface):

* tenor axis is exactly the pinned eight, in order; a config drift fails loudly;
* delta band spans 30Δ-put → ATM → 30Δ-call; out-of-band targets are labeled gaps;
* dollar Greeks equal the hand-computed values; the gamma-1% and theta-365 flags move
  exactly their own number; every dollar field carries its unit string beside the decimals;
* the tenor regrid is calendar-no-arb (Hypothesis property test, Eq 21);
* no look-ahead (a later snapshot's fits do not change a cell);
* golden grid byte-identical, with a cross-process stamp-hash check (no PYTHONHASHSEED);
* reordering the input slices leaves the grid identical;
* edge cases: empty, single-expiry (cannot span → labeled gaps), tenor beyond span,
  NaN/inf inputs rejected;
* the C->A storage round-trip and write-ahead rejection of a malformed cell;
* two providers writing the same (underlying, trade_date) land in disjoint partitions.
"""

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
from algotrading.infra.pricing import UNIT_STRINGS, dollar_greeks
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


# --------------------------------------------------------------------------- #
# Building the projection inputs from the calendar-consistent oracle           #
# --------------------------------------------------------------------------- #
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
    """Fit every slice of the synthetic term surface (the real surface engine)."""
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


# --------------------------------------------------------------------------- #
# Tenor axis                                                                   #
# --------------------------------------------------------------------------- #
def test_tenor_grid_is_the_pinned_eight() -> None:
    # The authoritative axis (P0.1 / OQ-4): exactly these eight, in this order.
    assert PINNED_TENORS == ("10d", "1m", "3m", "6m", "12m", "18m", "2y", "3y")
    assert ProjectionConfig(version="v").tenor_grid == PINNED_TENORS


def test_tenor_grid_drift_fails_loudly() -> None:
    # A config drift to a different/reordered set is refused at construction, not silently
    # quoted on a wrong axis.
    with pytest.raises(ProjectionConfigError):
        ProjectionConfig(version="v", tenor_grid=("10d", "1m", "3m"))
    with pytest.raises(ProjectionConfigError):
        ProjectionConfig(version="v", tenor_grid=("1m", "10d", "3m", "6m", "12m", "18m", "2y", "3y"))


def test_emitted_cells_carry_only_pinned_tenor_labels() -> None:
    result = _project(build_synthetic_term_surface())
    assert {c.tenor_label for c in result.cells} <= set(PINNED_TENORS)
    # The cells come out in tenor order then band order (a pure function of the config axes).
    seen_tenors = [c.tenor_label for c in result.cells]
    order = {t: i for i, t in enumerate(PINNED_TENORS)}
    assert seen_tenors == sorted(seen_tenors, key=lambda t: order[t])


# --------------------------------------------------------------------------- #
# Delta band                                                                   #
# --------------------------------------------------------------------------- #
def test_delta_band_spans_30d_put_to_30d_call() -> None:
    # The broad-ladder term surface puts the whole 30Δ window inside the fitted span at the
    # interior tenors, so a representative tenor carries the full band: 30Δ put, ATM, 30Δ call.
    result = _project(build_synthetic_term_surface())
    by_tenor: dict[str, set[str]] = {}
    for c in result.cells:
        by_tenor.setdefault(c.tenor_label, set()).add(c.delta_band)
    # 1y is comfortably inside the fitted span [10d, 3y].
    assert {"30dp", "atm", "30dc"} <= by_tenor["12m"]


def test_atm_delta_is_near_half() -> None:
    # The ATM band point solves to a call delta near 0.5 (the engine's spot delta, which is
    # the discounted N(d1) ~ 0.5 at the money). An independent sanity bound, not a round-trip.
    result = _project(build_synthetic_term_surface())
    atm = next(c for c in result.cells if c.tenor_label == "12m" and c.delta_band == "atm")
    assert atm.target_delta == 0.0
    assert 0.45 <= atm.delta <= 0.55


def test_atm_put_pillar_shares_the_atm_call_strike() -> None:
    # The two legs of an ATM straddle: ``atm`` (call) and ``atmp`` (put) at the SAME ATM-forward
    # strike. Independent oracle: a straddle is two same-strike legs, so the strikes must be equal
    # (and the IV at one strike is one number, so the two cells share it).
    result = _project(build_synthetic_term_surface())
    atm = next(c for c in result.cells if c.tenor_label == "12m" and c.delta_band == "atm")
    atmp = next(c for c in result.cells if c.tenor_label == "12m" and c.delta_band == "atmp")
    assert atmp.target_delta == 0.0
    assert atmp.strike == pytest.approx(atm.strike)
    assert atmp.log_moneyness == pytest.approx(atm.log_moneyness)
    assert atmp.implied_vol == pytest.approx(atm.implied_vol)


def test_atm_put_pillar_is_a_put_with_matching_gamma_vega() -> None:
    # The ATM put has a negative spot delta near -0.5; being the same strike as the ATM call it
    # carries the same gamma and vega (those do not depend on call-vs-put). Oracle: option theory.
    result = _project(build_synthetic_term_surface())
    atm = next(c for c in result.cells if c.tenor_label == "12m" and c.delta_band == "atm")
    atmp = next(c for c in result.cells if c.tenor_label == "12m" and c.delta_band == "atmp")
    assert -0.55 <= atmp.delta <= -0.45
    assert atmp.gamma == pytest.approx(atm.gamma)
    assert atmp.vega == pytest.approx(atm.vega)


def test_atm_straddle_is_approximately_delta_neutral_and_double_gamma() -> None:
    # A long ATM straddle = long atm call + long atm put. Net dollar-delta is small relative to
    # either leg (the straddle's defining ~delta-neutrality), and gamma is ~2x a single leg.
    # Oracle: straddle delta = Δcall + Δput, which nearly cancels at the ATM-forward strike.
    result = _project(build_synthetic_term_surface())
    atm = next(c for c in result.cells if c.tenor_label == "12m" and c.delta_band == "atm")
    atmp = next(c for c in result.cells if c.tenor_label == "12m" and c.delta_band == "atmp")
    net_dollar_delta = atm.dollar_delta + atmp.dollar_delta
    assert abs(net_dollar_delta) < 0.30 * abs(atm.dollar_delta)
    assert (atm.dollar_gamma + atmp.dollar_gamma) == pytest.approx(2 * atm.dollar_gamma, rel=1e-6)


def test_out_of_band_target_is_a_labeled_gap_not_a_nan() -> None:
    # A 5Δ call at the short 10d tenor lands beyond the fitted strike span on this surface —
    # the projection must label it a gap, never emit a NaN-bearing cell.
    projection = ProjectionConfig(
        version="v", band_labels=("5dc",), band_targets=(0.05,),
    )
    result = _project(build_synthetic_term_surface(strikes=(95.0, 100.0, 105.0)),
                      projection=projection)
    # No emitted cell carries a NaN.
    for c in result.cells:
        assert math.isfinite(c.strike) and math.isfinite(c.implied_vol)
    # The unreachable target shows up as a labeled gap with a structured reason.
    assert result.gaps
    assert all(g.reason_code in {"delta_out_of_band", "tenor_beyond_span", "no_curve"}
               for g in result.gaps)
    assert any(g.reason_code == "delta_out_of_band" for g in result.gaps)


def test_iv_used_to_price_equals_iv_at_solved_strike() -> None:
    # No mismatch: a cell's implied_vol is the surface IV at its own solved log-moneyness.
    slices = _fit_term_surface(build_synthetic_term_surface())
    result = _project(build_synthetic_term_surface())
    for c in result.cells:
        w = interpolate_total_variance(slices, c.log_moneyness, c.maturity_years)
        expected_iv = math.sqrt(max(w, 0.0) / c.maturity_years)
        assert c.implied_vol == pytest.approx(expected_iv, rel=1e-9)
        assert c.total_variance == pytest.approx(c.implied_vol ** 2 * c.maturity_years, rel=1e-9)


# --------------------------------------------------------------------------- #
# Delta band — the ±30Δ pas-2 grid (band_step from typed config, ADR 0028)     #
# --------------------------------------------------------------------------- #
def test_band_axis_is_the_30d_pas2_grid() -> None:
    # Independent oracle: the generator must expand (-0.30, +0.30, 0.02) into the prof's
    # 30Δ-put → ATM → 30Δ-call window at step 2 — hand-listed here, not read from the code.
    labels, targets = delta_band_axis(band_low_delta=-0.30, band_high_delta=0.30, band_step=0.02)
    expected_puts = tuple(f"{m:02d}dp" for m in range(30, 1, -2))   # 30dp,28dp,…,02dp (15)
    expected_calls = tuple(f"{m:02d}dc" for m in range(2, 31, 2))   # 02dc,…,30dc (15)
    assert labels == expected_puts + ("atm", "atmp") + expected_calls
    assert len(labels) == 32
    assert len(set(labels)) == len(labels)  # labels unique
    # Targets: puts strictly increasing -0.30…-0.02, the two ATM pillars at 0.0, calls 0.02…0.30.
    assert targets[:15] == tuple(pytest.approx(-m / 100.0) for m in range(30, 1, -2))
    assert targets[15:17] == (0.0, 0.0)
    assert targets[17:] == tuple(pytest.approx(m / 100.0) for m in range(2, 31, 2))


def test_band_axis_rejects_off_grid_or_inverted_bands() -> None:
    # A step that does not divide the edge, a non-hundredth value, and an inverted band each
    # fail loudly (no silent off-grid axis) — ADR 0028.
    with pytest.raises(ProjectionConfigError):
        delta_band_axis(band_low_delta=-0.30, band_high_delta=0.30, band_step=0.025)  # 30 % 2.5
    with pytest.raises(ProjectionConfigError):
        delta_band_axis(band_low_delta=-0.301, band_high_delta=0.30, band_step=0.02)  # off 0.01
    with pytest.raises(ProjectionConfigError):
        delta_band_axis(band_low_delta=0.10, band_high_delta=0.30, band_step=0.02)  # low not < 0


def test_default_projection_offers_the_pas2_grid() -> None:
    # The default config's axis IS the prof's 32-cell pas-2 band (15 puts + atm + atmp + 15
    # calls) — the config offers every point.
    expected_labels, _ = delta_band_axis(band_low_delta=-0.30, band_high_delta=0.30, band_step=0.02)
    assert ProjectionConfig(version="v").band_labels == expected_labels
    assert len(expected_labels) == 32
    # End-to-end at an interior tenor (12m): the produced bands are a subset of the 32 (no
    # stray label), and the band core — both 30Δ edges and the two ATM pillars — is present.
    # The deepest 2Δ wings can fall outside the fitted strike span (labeled gaps, see
    # test_step2_deep_otm_extremes_are_labeled_gaps_not_nans), so completeness is a subset, not
    # the full set, on a finite strike ladder.
    result = _project(build_synthetic_term_surface())
    bands_12m = {c.delta_band for c in result.cells if c.tenor_label == "12m"}
    assert bands_12m <= set(expected_labels)
    assert {"30dp", "atm", "atmp", "30dc"} <= bands_12m


def test_solved_cells_realize_their_target_delta() -> None:
    # Independent oracle (norm-free, all 30 non-ATM points): the inversion solves the strike so
    # the option's realized spot delta is DF·|target| with the right sign — |Δput| and |Δcall|
    # at the configured band targets. DF = exp(-r·T) from the generator's flat rate; this uses
    # only put-call parity and the definition of delta, never the projection's own solver loop.
    term = build_synthetic_term_surface()
    result = _project(term)
    checked = 0
    for c in result.cells:
        if c.delta_band in {"atm", "atmp"}:
            continue
        df = math.exp(-term.rate * c.maturity_years)
        expected_abs = df * abs(c.target_delta)
        assert abs(c.delta) == pytest.approx(expected_abs, rel=1e-4, abs=1e-9), c.delta_band
        assert (c.delta < 0.0) == (c.target_delta < 0.0)  # put target → negative realized delta
        checked += 1
    assert checked >= 30  # every interior tenor carries the full 30 non-ATM points


def test_strikes_are_monotone_in_target_nd1() -> None:
    # N(d1) is monotone decreasing in strike, so ordering the cells of one tenor by their
    # target N(d1) (descending) must give non-decreasing strikes — an independent monotonicity
    # oracle over the whole 32-point band. The two ATM pillars share N(d1)=0.5 and one strike.
    result = _project(build_synthetic_term_surface())
    cells_12m = [c for c in result.cells if c.tenor_label == "12m"]

    def target_nd1(t: float) -> float:
        return 0.5 if t == 0.0 else (t if t > 0.0 else 1.0 + t)

    ordered = sorted(cells_12m, key=lambda c: target_nd1(c.target_delta), reverse=True)
    strikes = [c.strike for c in ordered]
    assert strikes == sorted(strikes)  # non-decreasing (strict but for the atm/atmp tie)


def test_step2_deep_otm_extremes_are_labeled_gaps_not_nans() -> None:
    # On a narrow strike ladder the deepest-OTM pas-2 bands (the 2Δ wings — lowest/highest
    # strikes) fall outside the fitted strike span and must be labeled delta_out_of_band gaps,
    # never NaN cells. The near-ATM bands still produce.
    result = _project(build_synthetic_term_surface(strikes=(95.0, 100.0, 105.0)))
    for c in result.cells:
        assert math.isfinite(c.strike) and math.isfinite(c.implied_vol)
    deep_gaps = {g.delta_band for g in result.gaps if g.reason_code == "delta_out_of_band"}
    assert {"02dp", "02dc"} & deep_gaps  # at least one 2Δ wing is an out-of-band gap
    assert all(
        g.reason_code in {"delta_out_of_band", "tenor_beyond_span", "no_curve"}
        for g in result.gaps
    )


# --------------------------------------------------------------------------- #
# Dollar Greeks — independent hand oracle                                      #
# --------------------------------------------------------------------------- #
def test_dollar_greeks_match_hand_values() -> None:
    # Independent oracle: hand-compute the five $-Greeks from the cell's own decimal Greeks
    # and spot, with the pinned-default flags (gamma per 1% => /100, theta /365), mult=1.
    #   dollar_delta = Δ · S            dollar_gamma = Γ · S² / 100
    #   dollar_vega  = Vega · 0.01      dollar_theta = Θ / 365
    #   dollar_rho   = Rho · 0.01
    result = _project(build_synthetic_term_surface())
    cell = result.cells[len(result.cells) // 2]  # an interior cell
    s = cell.forward_price  # carry == 0 here, so spot == forward
    assert cell.dollar_delta == pytest.approx(cell.delta * s, rel=1e-12)
    assert cell.dollar_gamma == pytest.approx(cell.gamma * s * s / 100.0, rel=1e-12)
    assert cell.dollar_vega == pytest.approx(cell.vega * 0.01, rel=1e-12)
    assert cell.dollar_theta == pytest.approx(cell.theta / 365.0, rel=1e-12)
    assert cell.dollar_rho == pytest.approx(cell.rho * 0.01, rel=1e-12)


def test_dollar_greeks_match_standalone_dollar_greeks_engine() -> None:
    # The projection must reuse the one dollar-Greek home (pricing.dollar_greeks), so a cell
    # equals a direct call to it on the same decimals — no forked second formula.
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
    # Flipping gamma_normalisation from one_pct (/100) to one_dollar (×1) scales exactly the
    # dollar gamma by 100 and leaves the other four dollar numbers untouched.
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
    # Flipping theta_day_count from 365 to 252 scales exactly the dollar theta by 365/252 and
    # leaves the others untouched.
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
    # Every dollar field has its expected unit string, and the decimal per-unit Greeks sit
    # beside them (both representations side by side on the row).
    cell = _project(build_synthetic_term_surface()).cells[0]
    assert cell.dollar_delta_unit == UNIT_STRINGS["dollar_delta"]
    assert cell.dollar_gamma_unit == UNIT_STRINGS["dollar_gamma_one_pct"]
    assert cell.dollar_vega_unit == UNIT_STRINGS["dollar_vega"]
    assert cell.dollar_theta_unit == UNIT_STRINGS["dollar_theta_365"]
    assert cell.dollar_rho_unit == UNIT_STRINGS["dollar_rho"]
    # Decimal Greeks present and finite beside the dollar layer.
    for name in ("delta", "gamma", "vega", "theta", "rho"):
        assert math.isfinite(getattr(cell, name))


# --------------------------------------------------------------------------- #
# Calendar no-arb property (Eq 21)                                             #
# --------------------------------------------------------------------------- #
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
    # Over random calendar-consistent fitted slices, the regridded total variance must be
    # non-decreasing as the target maturity rises at a fixed log-moneyness (Eq 21). The
    # generator builds w(k,T) increasing in T; the regrid must preserve it.
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
    # The regrid reproduces the generator's true total variance at an interior tenor, within
    # the SVI fit tolerance — checked against the independent oracle, not the regrid itself.
    term = build_synthetic_term_surface()
    slices = _fit_term_surface(term)
    for k in (-0.2, 0.0, 0.2):
        for maturity in (0.5, 1.0, 2.0):
            got = interpolate_total_variance(slices, k, maturity)
            expected = term.true_total_variance(k, maturity)
            assert got == pytest.approx(expected, abs=2e-3)


# --------------------------------------------------------------------------- #
# No look-ahead                                                                #
# --------------------------------------------------------------------------- #
def test_no_lookahead_in_projection() -> None:
    # A cell at snapshot D depends only on D's fits/state. Re-stamping the very same fits at a
    # later snapshot ts changes only the timestamps, never the grid's economic numbers —
    # there is no path by which a future observation could enter a D cell.
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


# --------------------------------------------------------------------------- #
# Reordering invariance                                                        #
# --------------------------------------------------------------------------- #
def test_reordering_invariance() -> None:
    # Shuffling the input fits (and the per-slice strikes) leaves the grid identical: cell
    # ordering follows the config axes and the stamp sorts its sources canonically.
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


# --------------------------------------------------------------------------- #
# Edge cases                                                                   #
# --------------------------------------------------------------------------- #
def test_empty_chain_yields_all_gaps_no_cells() -> None:
    # No fitted slices at all: every (tenor, band) point is a labeled gap, no cell, no crash.
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
    # One listed maturity (1y) carries a curve only at 1y; every other pinned tenor is outside
    # the (degenerate) fitted span and must be a labeled gap, never an extrapolation.
    term = build_synthetic_term_surface(maturities=(1.0,))
    result = _project(term)
    produced_tenors = {c.tenor_label for c in result.cells}
    # 1y matches the single fitted maturity exactly; tenors away from 1y are gaps.
    assert "12m" in produced_tenors
    assert any(g.tenor_label == "3y" and g.reason_code == "tenor_beyond_span" for g in result.gaps)
    assert any(g.tenor_label == "10d" and g.reason_code == "tenor_beyond_span" for g in result.gaps)


def test_tenor_beyond_span_is_a_labeled_gap() -> None:
    # A surface fitted only out to 1y leaves 18m/2y/3y beyond span — labeled, not guessed.
    term = build_synthetic_term_surface(maturities=(10.0 / 365.0, 0.5, 1.0))
    result = _project(term)
    long_gaps = {g.tenor_label for g in result.gaps if g.reason_code == "tenor_beyond_span"}
    assert {"18m", "2y", "3y"} <= long_gaps
    # Nothing beyond the span was emitted as a cell.
    assert not any(c.tenor_label in {"18m", "2y", "3y"} for c in result.cells)


def test_strike_exactly_at_band_edge_is_kept() -> None:
    # The 30Δ band edge is the ">=" boundary; the ATM/30Δ pillars are produced (not dropped)
    # at an interior tenor where they fall inside the fitted span.
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
    # A degenerate (empty -> insufficient) slice sitting beside curve-bearing ones is ignored
    # by the strike-span/maturity-span scans, never crashing the regrid.
    term = build_synthetic_term_surface()
    slices = list(_fit_term_surface(term))
    empty_slice = fit_slice("AAPL", 5.0, (), expiry_date=EXPIRY, day_count="ACT/365",
                            config=SURFACE_CONFIG)
    slices.append(empty_slice)  # insufficient: carries no curve, no strikes
    result = project_grid(
        slices, _market(term), snapshot_ts=TS, source_snapshot_ts=TS, calc_ts=TS,
        projection=ProjectionConfig(version="p"), monetization=MonetizationConfig(version="m"),
        config_hashes=CONFIG_HASHES,
    )
    assert result.cells  # the curve-bearing slices still produce a grid
    # The bogus 5y insufficient slice did not widen the span: 3y stays the long end.
    assert empty_slice.method == "insufficient"


def test_solver_returns_none_when_target_unbracketed() -> None:
    # Direct unit test of the delta -> strike inversion: a target N(d1) outside the span's
    # endpoints is out of band and returns None (a labeled gap), never an extrapolated strike.
    from algotrading.infra.surfaces.projection import _solve_strike_for_delta
    slices = _fit_term_surface(build_synthetic_term_surface(strikes=(95.0, 100.0, 105.0)))
    # A 1Δ call target needs a strike far above the narrow [95, 105] span -> unbracketed.
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


# --------------------------------------------------------------------------- #
# Discount-factor curve resolution (F-SURF-01)                                 #
# --------------------------------------------------------------------------- #
def _listed_expiry_market(term: SyntheticTermSurface) -> SnapshotMarketState:
    """A market state whose DF curve is keyed by the LISTED-EXPIRY maturities.

    This is the shape the live driver builds (``_build_projected_analytics`` keys the
    curve by ``ForwardEstimate.maturity_years``), which the pinned-tenor queries never
    hit exactly — the F-SURF-01 regression shape.
    """
    return SnapshotMarketState(
        underlying="AAPL", provider="DERIBIT", spot=term.forward,
        discount_factors={
            round(t, 9): math.exp(-term.rate * t) for t in term.maturities
        },
        default_discount_factor=1.0,
    )


def test_discount_factor_exact_key_hit_returns_the_stored_value() -> None:
    # An exact knot query returns the stored factor bit-for-bit (no log/exp round-trip),
    # preserving byte-identical behavior for curves already keyed at the query points.
    market = SnapshotMarketState(
        underlying="AAPL", provider="DERIBIT", spot=100.0,
        discount_factors={0.5: 0.99123456789, 1.0: 0.97},
    )
    assert market.discount_factor_at(0.5) == 0.99123456789
    assert market.discount_factor_at(1.0) == 0.97


def test_discount_factor_flat_rate_listed_curve_recovers_the_rate_at_every_tenor() -> None:
    # Oracle: a flat 2% curve has DF(T) = exp(-0.02·T) at EVERY maturity, by definition.
    # The curve is keyed by listed expiries (10d, 0.5y, 1y, 2y, 3y); the eight pinned
    # tenors mostly fall between those knots. Linear interpolation of -ln DF is exact for
    # a flat rate (collinear knots), so every tenor must recover exp(-0.02·T) — not 1.0.
    # rel=1e-9, not 1e-12: the driver keys each knot at round(T, 9) while the factor is
    # computed at the true T, so the curve itself carries an O(1e-10·r) inconsistency.
    term = build_synthetic_term_surface()
    market = _listed_expiry_market(term)
    for label in PINNED_TENORS:
        maturity = tenor_years(label)
        assert market.discount_factor_at(maturity) == pytest.approx(
            math.exp(-term.rate * maturity), rel=1e-9
        ), label


def test_discount_factor_interpolates_log_linearly_between_knots() -> None:
    # Non-flat curve: knots at (T=1, DF=0.98) and (T=2, DF=0.94). Hand oracle for T=1.25:
    # y(T) = -ln DF is interpolated linearly: y = y1 + 0.25·(y2 - y1)
    #      = -ln(0.98) + 0.25·(-ln(0.94) + ln(0.98)); DF = exp(-y).
    market = SnapshotMarketState(
        underlying="AAPL", provider="DERIBIT", spot=100.0,
        discount_factors={1.0: 0.98, 2.0: 0.94},
    )
    y1, y2 = -math.log(0.98), -math.log(0.94)
    expected = math.exp(-(y1 + 0.25 * (y2 - y1)))
    assert market.discount_factor_at(1.25) == pytest.approx(expected, rel=1e-12)


def test_discount_factor_extrapolates_flat_zero_rate_beyond_the_knot_span() -> None:
    # Beyond the ends the nearest knot's zero rate is held flat: r = -ln(DF)/T at the
    # boundary knot, DF(T) = exp(-r·T). Short of the first knot this tends to DF(0) = 1,
    # never a frozen DF (which would mis-discount a 10d cell with a 1y factor).
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
    # The label-keyed curve is the join that cannot drift through float re-derivation:
    # when a tenor-labeled factor is present it is used verbatim, even when the
    # maturity-keyed curve would interpolate to a different value.
    market = SnapshotMarketState(
        underlying="AAPL", provider="DERIBIT", spot=100.0,
        discount_factors={1.0: 0.98, 2.0: 0.94},
        discount_factors_by_tenor={"18m": 0.9123},
    )
    assert market.discount_factor_for("18m", tenor_years("18m")) == 0.9123
    # A label without an entry falls through to the maturity curve.
    assert market.discount_factor_for("12m", 1.0) == 0.98


def test_discount_factor_empty_curve_falls_back_to_the_default() -> None:
    # The documented no-curve degradation: with no usable forward estimates at all the
    # explicit default applies. This is the only remaining fallback path.
    market = SnapshotMarketState(
        underlying="AAPL", provider="DERIBIT", spot=100.0, default_discount_factor=0.97,
    )
    assert market.discount_factor_at(1.0) == 0.97


def test_projection_prices_with_the_listed_expiry_curve_not_rate_free() -> None:
    # The end-to-end F-SURF-01 regression: projecting against the listed-expiry-keyed
    # curve must price each cell with the same discounting as the pinned-keyed curve
    # (both encode the identical flat 2% rate) — before the fix the listed-keyed run
    # silently priced every cell at DF=1.0.
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
        ),
        **kwargs,
    )
    for got, want in zip(listed.cells, pinned.cells, strict=True):
        assert got.price == pytest.approx(want.price, rel=1e-9)
        assert got.delta == pytest.approx(want.delta, rel=1e-9)
        assert got.rho == pytest.approx(want.rho, rel=1e-9)
    # And the discounting is real: the rate-free grid prices the long-dated ATM call higher.
    atm_3y = next(c for c in listed.cells if c.tenor_label == "3y" and c.delta_band == "atm")
    atm_3y_free = next(
        c for c in rate_free.cells if c.tenor_label == "3y" and c.delta_band == "atm"
    )
    assert atm_3y.price < atm_3y_free.price


# --------------------------------------------------------------------------- #
# C -> A storage seam                                                          #
# --------------------------------------------------------------------------- #
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
    # A non-finite implied_vol must be refused at the write door with an explicit error,
    # never silently coerced (TESTING.md: at least one malformed instance per contract).
    store = ParquetStore(tmp_path)
    good = _project(build_synthetic_term_surface()).cells[0]
    bad = dataclasses.replace(good, implied_vol=float("nan"))
    with pytest.raises(ContractValidationError):
        store.write("projected_option_analytics", [bad])
    # A negative strike (must be strictly positive) is also refused.
    bad_strike = dataclasses.replace(good, strike=-1.0)
    with pytest.raises(ContractValidationError):
        store.write("projected_option_analytics", [bad_strike])


def test_two_providers_land_in_disjoint_partitions(tmp_path: Path) -> None:
    # D1 invariant: provider is a partition segment, so two sources of the same
    # (underlying, trade_date) coexist and a provider-scoped read returns only its own.
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


# --------------------------------------------------------------------------- #
# Golden grid + cross-process stamp hash                                       #
# --------------------------------------------------------------------------- #
def compute_grid_summary() -> dict[str, Any]:
    """Run the projection on the fixed term surface and summarize for the golden artifact.

    Shared by the golden test and the cross-process subprocess so both exercise the same
    path. Keyed by (tenor, band) so the artifact is stable and human-readable.
    """
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
    # Recompute in a separate interpreter with no PYTHONHASHSEED: the config_hashes dict and
    # source-record set must hash identically across processes (the classic salted-hash bug).
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
    # The projection-axis hash itself must be byte-identical across processes (canonical_json,
    # no salted hash()). Compute it in a subprocess and compare.
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
    # And the hash collapses -0.0 onto 0.0 (the C7 -0.0 discipline).
    minus_zero = ProjectionConfig(version="proj-test", band_labels=("atm",), band_targets=(-0.0,))
    plus_zero = ProjectionConfig(version="proj-test", band_labels=("atm",), band_targets=(0.0,))
    assert minus_zero.config_hash() == plus_zero.config_hash()
    assert "NaN" not in canonical_json(ProjectionConfig(version="proj-test"))


def test_projection_config_hashes_match_the_pinned_golden_digests() -> None:
    # Golden-hash pins (M14/M25) that freeze the bytes of the `projection` and `scenarios`
    # bundle hashes entering every cell's provenance config_hashes. Normally: if one moves,
    # revert — never regenerate.
    #
    # The `projection` digest was regenerated ONCE, by design, on 2026-06-13: the owner
    # (Vincent) ruled the default band to the prof's ±30Δ *pas-2* grid (band_step in typed
    # config; 15 puts + atm + atmp + 15 calls). This is a pre-capture economic change with NO
    # banked record to protect (ADR 0028 / C7 pattern, same as the delta-window/universe regen).
    # The `scenarios` digest is unchanged — MonetizationConfig did not move.
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
    # M14: projection.py used to re-declare PRICER_VERSION as a string literal that
    # merely "mirrored" pricing.engine's — a double-edit hazard (the black76-crr misnomer
    # correction would today have to hit two files). The projection must now carry the
    # very same object the engine exports, and the persisted value is pinned to the
    # string every existing ProjectedOptionAnalytics row carries.
    from algotrading.infra.pricing import PRICER_VERSION as engine_version
    from algotrading.infra.surfaces import projection as projection_module

    assert projection_module.PRICER_VERSION is engine_version
    assert engine_version == "black76-lr-1.0.0"
