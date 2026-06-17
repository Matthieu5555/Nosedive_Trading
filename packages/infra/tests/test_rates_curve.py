"""Tests for the per-currency risk-free curve r(T) ingest, evaluation, Rho basis, spread QC.

Expected values are derived independently of the code under test (by hand / from first principles),
with float tolerances. Includes an explicit no-look-ahead/as-of point-in-time read test.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime

import pytest
from algotrading.core.config import CurrencyRateConfig, RatePillarConfig
from algotrading.infra.contracts import RiskFreeRatePoint
from algotrading.infra.pricing.black76 import price_european
from algotrading.infra.pricing.state import from_forward
from algotrading.infra.rates import (
    RateCurve,
    RateCurveError,
    build_rate_points,
    curve_from_points,
    external_curve_rho,
    implied_riskfree_spread,
    to_continuous_act365,
)
from algotrading.infra.rates.conventions import RateConventionError
from algotrading.infra.rates.ingest import RateIngestError
from algotrading.infra.rates.spread import QC_FAIL, QC_OK, QC_WARN

_AS_OF = date(2026, 6, 17)
_SNAP = datetime(2026, 6, 17, 15, 30, tzinfo=UTC)
_CALC = datetime(2026, 6, 17, 15, 30, 5, tzinfo=UTC)
_HASHES = {"rates": "rates-hash-0"}


# --- curve evaluation: linear in zero rate, flat extrapolation ------------------------------------


def test_rate_at_interpolates_linearly_in_the_zero_rate() -> None:
    # Two pillars: r(0.25)=0.03, r(1.0)=0.05. At T=0.5 the linear-in-rate value is
    # 0.03 + (0.5-0.25)/(1.0-0.25) * (0.05-0.03) = 0.03 + (0.25/0.75)*0.02 = 0.0366666...
    curve = RateCurve.from_pillars("EUR", [(0.25, 0.03), (1.0, 0.05)])
    expected = 0.03 + (0.5 - 0.25) / (1.0 - 0.25) * (0.05 - 0.03)
    assert curve.rate_at(0.5) == pytest.approx(expected, abs=1e-12)
    # Exactly on a pillar returns that pillar.
    assert curve.rate_at(0.25) == pytest.approx(0.03, abs=1e-12)
    assert curve.rate_at(1.0) == pytest.approx(0.05, abs=1e-12)


def test_rate_at_flat_extrapolates_beyond_the_ends() -> None:
    curve = RateCurve.from_pillars("EUR", [(0.25, 0.03), (1.0, 0.05)])
    assert curve.rate_at(0.01) == pytest.approx(0.03, abs=1e-12)  # below first pillar -> first rate
    assert curve.rate_at(5.0) == pytest.approx(0.05, abs=1e-12)   # above last pillar -> last rate


def test_flat_curve_is_constant_everywhere() -> None:
    curve = RateCurve.flat("EUR", 0.042)
    for t in (0.01, 0.25, 1.0, 3.0, 10.0):
        assert curve.rate_at(t) == pytest.approx(0.042, abs=1e-12)


def test_discount_factor_is_continuous_act365() -> None:
    curve = RateCurve.flat("EUR", 0.05)
    # DF(2y) = exp(-0.05 * 2) = exp(-0.10)
    assert curve.discount_factor(2.0) == pytest.approx(math.exp(-0.10), abs=1e-12)


def test_curve_rejects_unsorted_or_empty() -> None:
    with pytest.raises(RateCurveError):
        RateCurve(currency="EUR", pillars=())
    with pytest.raises(RateCurveError):
        # duplicate maturity is not strictly increasing
        RateCurve.from_pillars("EUR", [(1.0, 0.05), (1.0, 0.04)]).rate_at(1.0)


# --- convention conversion: money-market -> continuous ACT/365 ------------------------------------


def test_simple_act360_converts_through_the_growth_factor() -> None:
    # Source: simple 3% under ACT/360 for the 3m pillar (maturity_years = 0.25 under ACT/365).
    # tau_src = 0.25 * 365/360 ; growth = 1 + 0.03 * tau_src ; r_c = ln(growth)/0.25.
    tau_src = 0.25 * 365.0 / 360.0
    expected = math.log(1.0 + 0.03 * tau_src) / 0.25
    got = to_continuous_act365(
        0.03, 0.25, source_day_count="ACT/360", source_compounding="simple"
    )
    assert got == pytest.approx(expected, abs=1e-12)
    # ACT/360 accrues over a larger fraction than ACT/365, so the canonical rate sits just ABOVE
    # the 3% nominal (the day-count uplift dominates the small simple->continuous discount here).
    assert got > 0.03


def test_continuous_act365_source_is_identity() -> None:
    got = to_continuous_act365(
        0.037, 1.0, source_day_count="ACT/365", source_compounding="continuous"
    )
    assert got == pytest.approx(0.037, abs=1e-15)


def test_continuous_act360_rebases_day_count_only() -> None:
    # Continuous source under ACT/360 -> r_c = r_src * (365/360) (no growth-factor log).
    expected = 0.04 * 365.0 / 360.0
    got = to_continuous_act365(
        0.04, 0.5, source_day_count="ACT/360", source_compounding="continuous"
    )
    assert got == pytest.approx(expected, abs=1e-12)


def test_conversion_rejects_bad_inputs() -> None:
    with pytest.raises(RateConventionError):
        to_continuous_act365(0.03, 0.25, source_day_count="ACT/999", source_compounding="simple")
    with pytest.raises(RateConventionError):
        to_continuous_act365(0.03, -1.0, source_day_count="ACT/365", source_compounding="simple")


# --- ingest: config + published levels -> RiskFreeRatePoint rows ----------------------------------


def _eur_config() -> CurrencyRateConfig:
    return CurrencyRateConfig(
        currency="EUR",
        source="estr_euribor_ois",
        day_count="ACT/360",
        compounding="simple",
        interpolation="linear_zero",
        spread_qc_abs_bound=0.02,
        spread_qc_disposition="warn",
        pillars=(
            RatePillarConfig(tenor_label="3m", maturity_years=0.25, instrument="euribor_3m"),
            RatePillarConfig(tenor_label="12m", maturity_years=1.0, instrument="euribor_12m"),
        ),
    )


def test_build_rate_points_converts_and_stamps_each_pillar() -> None:
    points = build_rate_points(
        currency_config=_eur_config(),
        published_levels={"euribor_3m": 0.03, "euribor_12m": 0.035},
        as_of=_AS_OF,
        snapshot_ts=_SNAP,
        source_snapshot_ts=_SNAP,
        calc_ts=_CALC,
        config_hashes=_HASHES,
    )
    assert [p.pillar_tenor for p in points] == ["3m", "12m"]
    assert all(p.day_count == "ACT/365" for p in points)
    assert all(p.currency == "EUR" and p.as_of == _AS_OF for p in points)
    # 3m point converted exactly like the standalone converter.
    expected_3m = to_continuous_act365(
        0.03, 0.25, source_day_count="ACT/360", source_compounding="simple"
    )
    assert points[0].rate == pytest.approx(expected_3m, abs=1e-12)
    # The source convention is recorded on diagnostics; the as_of is stamped on provenance.
    assert points[0].diagnostics.source_day_count == "ACT/360"
    assert points[0].provenance.as_of == _AS_OF


def test_build_rate_points_skips_pillars_with_no_published_level() -> None:
    points = build_rate_points(
        currency_config=_eur_config(),
        published_levels={"euribor_3m": 0.03},  # 12m missing -> coverage gap, not a defect
        as_of=_AS_OF,
        snapshot_ts=_SNAP,
        source_snapshot_ts=_SNAP,
        calc_ts=_CALC,
        config_hashes=_HASHES,
    )
    assert [p.pillar_tenor for p in points] == ["3m"]


def test_curve_from_points_round_trips_through_evaluation() -> None:
    points = build_rate_points(
        currency_config=_eur_config(),
        published_levels={"euribor_3m": 0.03, "euribor_12m": 0.035},
        as_of=_AS_OF,
        snapshot_ts=_SNAP,
        source_snapshot_ts=_SNAP,
        calc_ts=_CALC,
        config_hashes=_HASHES,
    )
    curve = curve_from_points("EUR", points)
    # Evaluating at a pillar returns its converted rate.
    assert curve.rate_at(0.25) == pytest.approx(points[0].rate, abs=1e-12)


def test_curve_from_points_rejects_a_foreign_currency() -> None:
    points = build_rate_points(
        currency_config=_eur_config(),
        published_levels={"euribor_3m": 0.03},
        as_of=_AS_OF,
        snapshot_ts=_SNAP,
        source_snapshot_ts=_SNAP,
        calc_ts=_CALC,
        config_hashes=_HASHES,
    )
    with pytest.raises(RateIngestError):
        curve_from_points("USD", points)


# --- no look-ahead: an as-of read uses only the curve published as-of that day --------------------


def _point(as_of: date, rate: float) -> RiskFreeRatePoint:
    (p,) = build_rate_points(
        currency_config=CurrencyRateConfig(
            currency="EUR",
            source="estr_euribor_ois",
            day_count="ACT/365",
            compounding="continuous",
            pillars=(
                RatePillarConfig(tenor_label="3m", maturity_years=0.25, instrument="euribor_3m"),
            ),
        ),
        published_levels={"euribor_3m": rate},
        as_of=as_of,
        snapshot_ts=datetime(as_of.year, as_of.month, as_of.day, 15, 30, tzinfo=UTC),
        source_snapshot_ts=datetime(as_of.year, as_of.month, as_of.day, 15, 30, tzinfo=UTC),
        calc_ts=datetime(as_of.year, as_of.month, as_of.day, 15, 30, 5, tzinfo=UTC),
        config_hashes=_HASHES,
    )
    return p


def test_as_of_read_uses_only_the_curve_published_as_of_that_day() -> None:
    # Two days of published curves: D1 at 3%, D2 (later) at 5%. Reconstructing D1 must read ONLY the
    # D1 curve — joining D2's later curve onto a D1 valuation would be look-ahead.
    d1, d2 = date(2026, 6, 16), date(2026, 6, 17)
    history = [_point(d1, 0.03), _point(d2, 0.05)]

    def read_as_of(valuation_day: date) -> RateCurve:
        # The as-of filter: only rows published on/before the valuation day, taking the latest.
        visible = [p for p in history if p.as_of <= valuation_day]
        latest = max(p.as_of for p in visible)
        return curve_from_points("EUR", [p for p in visible if p.as_of == latest])

    assert read_as_of(d1).rate_at(0.25) == pytest.approx(0.03, abs=1e-12)
    assert read_as_of(d2).rate_at(0.25) == pytest.approx(0.05, abs=1e-12)
    # A valuation BEFORE any published curve has nothing to read (no look-ahead into the future).
    with pytest.raises(ValueError):
        read_as_of(date(2026, 6, 1))


# --- implied - risk-free spread diagnostic + warn-only QC -----------------------------------------


def test_spread_is_implied_minus_riskfree_and_ok_within_bound() -> None:
    diag = implied_riskfree_spread(
        currency="EUR",
        maturity_years=0.25,
        implied_rate=0.041,
        risk_free_rate=0.030,
        abs_bound=0.02,
    )
    assert diag.spread == pytest.approx(0.011, abs=1e-12)
    assert diag.breached is False
    assert diag.qc_status == QC_OK
    assert diag.label == "implied_riskfree_spread"


def test_spread_breach_warns_by_default_not_fails() -> None:
    diag = implied_riskfree_spread(
        currency="EUR",
        maturity_years=1.0,
        implied_rate=0.09,
        risk_free_rate=0.03,
        abs_bound=0.02,
    )
    assert diag.spread == pytest.approx(0.06, abs=1e-12)
    assert diag.breached is True
    assert diag.qc_status == QC_WARN  # warn-only default disposition


def test_spread_breach_can_be_configured_to_fail() -> None:
    diag = implied_riskfree_spread(
        currency="EUR",
        maturity_years=1.0,
        implied_rate=0.09,
        risk_free_rate=0.03,
        abs_bound=0.02,
        disposition="fail",
    )
    assert diag.qc_status == QC_FAIL


# --- external-curve Rho ---------------------------------------------------------------------------


def _atm_state(maturity: float, rate: float):
    # Forward at-the-money; discount factor consistent with the rate so the base state is coherent.
    spot = 100.0
    forward = spot  # carry 0 => ATM-forward = spot
    df = math.exp(-rate * maturity)
    return from_forward(
        forward=forward,
        strike=100.0,
        maturity_years=maturity,
        volatility=0.20,
        discount_factor=df,
        option_right="C",
        spot=spot,
    )


def test_external_curve_rho_matches_analytic_minus_t_times_price() -> None:
    # For a European call priced off the forward, holding the forward fixed and bumping only the
    # discounting rate r, Price = DF * (F*N(d1) - K*N(d2)) with DF = exp(-rT). So
    # dPrice/dr = -T * Price exactly. The finite-difference external rho must recover that.
    maturity, rate = 1.0, 0.03
    state = _atm_state(maturity, rate)
    curve = RateCurve.flat("EUR", rate)
    base_price = price_european(state).price
    rho = external_curve_rho(state, curve)
    assert rho == pytest.approx(-maturity * base_price, rel=1e-6)


def test_external_curve_rho_is_zero_at_expiry() -> None:
    # maturity 0 -> no rate sensitivity. Build a degenerate state by hand via from_forward at T>0
    # then assert the guard: we use a tiny maturity to keep the state valid and a flat curve.
    state = _atm_state(1e-9, 0.03)
    curve = RateCurve.flat("EUR", 0.03)
    rho = external_curve_rho(state, curve)
    assert rho == pytest.approx(0.0, abs=1e-6)
