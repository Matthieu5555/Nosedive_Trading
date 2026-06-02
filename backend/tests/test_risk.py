"""Step 11 — per-position Greeks, monetization, aggregation, reconciliation, edges.

Independent oracle (never the code under test): per-unit prices and Greeks derived
by two independent engines that agreed to ~1e-14 — a hand-coded generalized
Black-Scholes-Merton (Haug) and QuantLib 1.42.1's ``BlackCalculator`` /
``AnalyticEuropeanEngine`` — cross-checked against py_vollib prices to ~1e-13.
Market state: spot 100, carry 0 (so forward 100), T 0.25, sigma 0.20, DF 0.99
(r = -ln(0.99)/0.25), multiplier 100, USD, European.

Conventions asserted here: delta = dPrice/dspot; gamma = d2Price/dspot2; vega per
1.00 of vol; theta = dPrice/dt per year (negative for a long option). Monetization:
dollar_delta = delta*spot*M*Q, dollar_gamma = gamma*spot**2*M*Q (Eq 17),
dollar_vega = vega*0.01*M*Q (Eq 18), dollar_theta = theta*M*Q.
"""

from __future__ import annotations

import dataclasses
import math

import pytest

from fixtures.positions import (
    CALL_100,
    CALL_105,
    EUR_CALL_100,
    LOW_CONFIDENCE_CALL,
    PUT_100,
    RISK_SPOT,
    RISK_VALUATIONS,
    risk_positions,
)
from risk import (
    DEFAULT_BUMPS,
    AggregationError,
    BrokerGreeks,
    ContractValuationInput,
    PositionRisk,
    ValuationError,
    aggregate_by_desk,
    aggregate_lines,
    central_difference_greeks,
    position_risk,
    reconcile,
)

# --- Oracle per-unit Greeks (see module docstring for derivation) ------------
# (delta, gamma, vega, theta, price), per unit of underlying, multiplier 1.
ORACLE: dict[str, tuple[float, float, float, float, float]] = {
    "AAPL|OPT|C|100": (0.514739417780, 0.039445947495, 19.722973747691, -7.730479276483, 3.947883555998),  # noqa: E501
    "AAPL|OPT|P|100": (-0.475260582220, 0.039445947495, 19.722973747691, -7.730479276483, 3.947883555998),  # noqa: E501
    "AAPL|OPT|C|105": (0.327421504816, 0.035884390396, 17.942195198065, -7.094731500472, 2.043378946520),  # noqa: E501
    "AAPL|OPT|P|95": (-0.283872870388, 0.033707978743, 16.853989371359, -6.666452096311, 1.869182615580),  # noqa: E501
}

# Portfolio aggregate, hand-summed across the three pf-risk lines (oracle):
# net_* = sum(per_unit_greek * multiplier * quantity).
NET_DELTA = 850.59616033
NET_GAMMA = 30.48829087
NET_VEGA = 15244.14543326
NET_THETA = -5993.65908838

# Per-position monetized dollar Greeks for P1 (+10 C100, multiplier 100), oracle:
P1_DOLLAR_DELTA = 51473.94177800
P1_DOLLAR_GAMMA = 394459.47495382
P1_DOLLAR_VEGA = 197.22973748
P1_DOLLAR_THETA = -7730.47927648


CONTRACTS = ["AAPL|OPT|C|100", "AAPL|OPT|P|100", "AAPL|OPT|C|105", "AAPL|OPT|P|95"]


def line_for(valuation: ContractValuationInput, quantity: float) -> PositionRisk:
    return position_risk(portfolio_id="pf-risk", quantity=quantity, valuation=valuation)


# --- Greeks vs independent oracle --------------------------------------------
@pytest.mark.parametrize("contract", CONTRACTS)
def test_per_unit_greeks_match_independent_oracle(contract: str) -> None:
    delta, gamma, vega, theta, price = ORACLE[contract]
    g = line_for(RISK_VALUATIONS[contract], 1.0).greeks
    assert g.price == pytest.approx(price, abs=1e-8)
    assert g.delta == pytest.approx(delta, abs=1e-8)
    assert g.gamma == pytest.approx(gamma, abs=1e-9)
    assert g.vega == pytest.approx(vega, abs=1e-6)
    assert g.theta == pytest.approx(theta, abs=1e-6)


def test_greek_signs_and_domains() -> None:
    # Sign/domain anchors the oracle confirmed: call delta in (0,1), put in (-1,0),
    # gamma > 0, vega > 0, theta < 0 for a long ATM call.
    call = line_for(CALL_100, 1.0).greeks
    put = line_for(PUT_100, 1.0).greeks
    assert 0.0 < call.delta < 1.0
    assert -1.0 < put.delta < 0.0
    assert call.gamma > 0.0 and put.gamma > 0.0
    assert call.vega > 0.0 and put.vega > 0.0
    assert call.theta < 0.0


# --- Analytic vs central-difference (the sign/unit catcher) ------------------
@pytest.mark.parametrize("contract", CONTRACTS)
def test_analytic_and_central_difference_greeks_agree(contract: str) -> None:
    # This test exists even though the production Greeks are analytic: a central
    # difference of the pricer's price, at the shared DEFAULT_BUMPS, must reproduce
    # the analytic delta/gamma/vega/theta — catching a sign or unit error. Tolerances
    # are the oracle's measured max FD error at these bumps, with headroom.
    valuation = RISK_VALUATIONS[contract]
    analytic = line_for(valuation, 1.0).greeks
    fd = central_difference_greeks(valuation, bumps=DEFAULT_BUMPS)
    assert fd.delta == pytest.approx(analytic.delta, abs=1e-8)
    assert fd.gamma == pytest.approx(analytic.gamma, abs=1e-6)
    assert fd.vega == pytest.approx(analytic.vega, abs=1e-6)
    assert fd.theta == pytest.approx(analytic.theta, abs=1e-5)


# --- Monetization ------------------------------------------------------------
def test_dollar_greeks_use_documented_conventions() -> None:
    # P1: +10 C100, multiplier 100 => scale 1000. Compare to the oracle dollar values.
    line = line_for(CALL_100, 10.0)
    assert line.scale == pytest.approx(1000.0)
    assert line.dollar_delta == pytest.approx(P1_DOLLAR_DELTA, rel=1e-7)
    assert line.dollar_gamma == pytest.approx(P1_DOLLAR_GAMMA, rel=1e-7)
    assert line.dollar_vega == pytest.approx(P1_DOLLAR_VEGA, rel=1e-7)
    assert line.dollar_theta == pytest.approx(P1_DOLLAR_THETA, rel=1e-7)
    # And exactly the closed-form conventions, not just the oracle numbers.
    g = line.greeks
    assert line.dollar_delta == pytest.approx(g.delta * RISK_SPOT * 1000.0)
    assert line.dollar_gamma == pytest.approx(g.gamma * RISK_SPOT * RISK_SPOT * 1000.0)
    assert line.dollar_vega == pytest.approx(g.vega * 0.01 * 1000.0)
    assert line.dollar_theta == pytest.approx(g.theta * 1000.0)


def test_dollar_gamma_and_vega_scale_exactly_with_multiplier() -> None:
    # A contract with multiplier 100 produces a dollar gamma (Eq 17) and dollar vega
    # (Eq 18) exactly 100x the per-unit (multiplier 1) value (oracle: residual 0).
    mult_1 = dataclasses.replace(CALL_100, multiplier=1.0)
    mult_100 = dataclasses.replace(CALL_100, multiplier=100.0)
    one = line_for(mult_1, 1.0)
    hundred = line_for(mult_100, 1.0)
    assert hundred.dollar_gamma == pytest.approx(100.0 * one.dollar_gamma)
    assert hundred.dollar_vega == pytest.approx(100.0 * one.dollar_vega)


# --- Aggregation -------------------------------------------------------------
def _all_lines() -> list[PositionRisk]:
    positions = risk_positions()
    return [line_for(RISK_VALUATIONS[p.contract_key], p.quantity) for p in positions]


def test_aggregate_equals_hand_summed_lines() -> None:
    # All three pf-risk lines share underlying AAPL, so aggregating by underlying
    # yields one group whose net Greeks equal the oracle hand sum.
    lines = _all_lines()
    groups = aggregate_lines(lines, portfolio_id="pf-risk", dimension="underlying")
    assert len(groups) == 1
    net = groups[0]
    assert net.group_key == "underlying:AAPL"
    assert net.net_delta == pytest.approx(NET_DELTA, rel=1e-7)
    assert net.net_gamma == pytest.approx(NET_GAMMA, rel=1e-7)
    assert net.net_vega == pytest.approx(NET_VEGA, rel=1e-7)
    assert net.net_theta == pytest.approx(NET_THETA, rel=1e-7)
    # Sum of line-level equals the aggregate, by construction.
    assert net.net_delta == pytest.approx(sum(line.position_delta for line in lines))


def test_aggregate_by_instrument_and_maturity_partition_the_book() -> None:
    lines = _all_lines()
    by_instrument = aggregate_lines(lines, portfolio_id="pf-risk", dimension="instrument")
    assert {g.group_key for g in by_instrument} == {
        "instrument:AAPL|OPT|C|100",
        "instrument:AAPL|OPT|P|100",
        "instrument:AAPL|OPT|C|105",
    }
    # Every line lands in exactly one group: the lines partition.
    assert sum(len(g.lines) for g in by_instrument) == len(lines)
    by_maturity = aggregate_lines(lines, portfolio_id="pf-risk", dimension="maturity")
    assert len(by_maturity) == 1  # all share T = 0.25
    assert by_maturity[0].group_key == "maturity:0.25"


def test_long_short_same_contract_nets_to_zero() -> None:
    # A long+short of the same contract nets to ~0 for every Greek (oracle: exactly 0).
    lines = [line_for(CALL_100, 7.0), line_for(CALL_100, -7.0)]
    net = aggregate_lines(lines, portfolio_id="pf-risk", dimension="instrument")[0]
    assert net.net_delta == pytest.approx(0.0, abs=1e-12)
    assert net.net_gamma == pytest.approx(0.0, abs=1e-12)
    assert net.net_vega == pytest.approx(0.0, abs=1e-12)
    assert net.net_theta == pytest.approx(0.0, abs=1e-12)


# --- Reconciliation ----------------------------------------------------------
def test_reconciliation_surfaces_a_breach_and_stays_quiet_within_threshold() -> None:
    line = line_for(CALL_100, 10.0)
    delta = line.greeks.delta
    # A broker delta off by more than the default 1e-3 threshold is surfaced.
    breached = reconcile(line, BrokerGreeks(contract_key=line.contract_key, delta=delta + 0.01))
    assert [d.greek for d in breached] == ["delta"]
    assert breached[0].abs_diff == pytest.approx(0.01, abs=1e-9)
    # A broker delta within threshold is not surfaced.
    within = reconcile(line, BrokerGreeks(contract_key=line.contract_key, delta=delta + 1e-4))
    assert within == []
    # A broker that returns no Greeks at all yields no breaches (absent != disagree).
    assert reconcile(line, BrokerGreeks(contract_key=line.contract_key)) == []


# --- Edge cases (the floor every module clears) ------------------------------
def test_empty_portfolio_aggregates_to_nothing() -> None:
    assert aggregate_lines([], portfolio_id="pf-risk", dimension="underlying") == []


def test_single_position_is_its_own_aggregate() -> None:
    lines = [line_for(CALL_100, 10.0)]
    net = aggregate_lines(lines, portfolio_id="pf-risk", dimension="underlying")[0]
    assert net.net_delta == pytest.approx(lines[0].position_delta)


def test_low_confidence_contract_is_priced_and_labelled_not_dropped() -> None:
    line = line_for(LOW_CONFIDENCE_CALL, 5.0)
    assert line.valuation.confidence == "low"
    assert line.greeks.price > 0.0  # still priced
    # It still aggregates — a low-confidence line is flagged, never silently omitted.
    net = aggregate_lines([line], portfolio_id="pf-risk", dimension="underlying")[0]
    assert net.lines[0].valuation.confidence == "low"


def test_multi_currency_aggregation_groups_by_desk() -> None:
    # A USD and a EUR contract on one desk: raw (un-dollarized) net sensitivities sum
    # across currencies; dollar monetization stays line-level and currency-tagged.
    usd = line_for(CALL_100, 1.0)
    eur = line_for(EUR_CALL_100, 1.0)
    groups = aggregate_by_desk(
        [usd, eur],
        portfolio_id="pf-risk",
        desk_of={usd.contract_key: "vol", eur.contract_key: "vol"},
    )
    assert len(groups) == 1
    assert groups[0].net_delta == pytest.approx(usd.position_delta + eur.position_delta)
    assert {line.valuation.currency for line in groups[0].lines} == {"USD", "EUR"}


def test_greeks_under_nonzero_carry_match_independent_and_central_difference() -> None:
    # The in-suite fixtures all use carry == 0, so they cannot distinguish a bug that
    # confuses carry with rate. This pins the carry machinery explicitly: a non-zero
    # carry b. Independent oracle for the PRICE: the fixture's own forward-form
    # Black-76 (different code) on the carry-implied forward F = spot*exp(b*T).
    from fixtures.synthetic import black_call

    carry = 0.05
    valuation = dataclasses.replace(CALL_100, carry=carry, multiplier=50.0)
    line = line_for(valuation, 4.0)
    forward = RISK_SPOT * math.exp(carry * valuation.maturity_years)
    oracle_price = black_call(
        forward, valuation.strike, valuation.maturity_years, valuation.volatility,
        valuation.discount_factor,
    )
    assert line.greeks.price == pytest.approx(oracle_price, rel=1e-10)
    # And the analytic Greeks still agree with a central difference under carry != 0.
    fd = central_difference_greeks(valuation, bumps=DEFAULT_BUMPS)
    assert fd.delta == pytest.approx(line.greeks.delta, abs=1e-8)
    assert fd.gamma == pytest.approx(line.greeks.gamma, abs=1e-6)
    assert fd.vega == pytest.approx(line.greeks.vega, abs=1e-6)
    assert fd.theta == pytest.approx(line.greeks.theta, abs=1e-5)
    # Monetization scales with a non-default multiplier (50) and quantity (4): scale 200.
    assert line.scale == pytest.approx(200.0)
    assert line.dollar_gamma == pytest.approx(line.greeks.gamma * RISK_SPOT * RISK_SPOT * 200.0)


def test_reconciliation_surfaces_a_nan_broker_greek() -> None:
    # A NaN from the broker is corrupt data, not agreement: it must be surfaced, since
    # abs(x - nan) > threshold is False and would otherwise read as "agrees".
    line = line_for(CALL_100, 10.0)
    breached = reconcile(line, BrokerGreeks(contract_key=line.contract_key, delta=math.nan))
    assert [d.greek for d in breached] == ["delta"]


@pytest.mark.parametrize(
    "field, value, bad_field",
    [
        ("multiplier", 0.0, "multiplier"),
        ("currency", "", "currency"),
        ("confidence", "x", "confidence"),
    ],
)
def test_valuation_rejects_malformed_input(field: str, value: object, bad_field: str) -> None:
    # Negative paths are first-class: a malformed valuation raises a labeled error.
    with pytest.raises(ValuationError) as info:
        dataclasses.replace(CALL_100, **{field: value})  # type: ignore[arg-type]
    assert info.value.field == bad_field


def test_unknown_grouping_dimension_is_a_labeled_error() -> None:
    with pytest.raises(AggregationError):
        aggregate_lines(_all_lines(), portfolio_id="pf-risk", dimension="sector")


def test_degenerate_zero_maturity_prices_to_intrinsic_without_crashing() -> None:
    # A contract expiring now (T=0) collapses to intrinsic; the engine stays total.
    expired = dataclasses.replace(CALL_105, maturity_years=0.0, discount_factor=1.0)
    line = line_for(expired, 1.0)
    assert line.greeks.price == pytest.approx(max(RISK_SPOT - 105.0, 0.0), abs=1e-12)
    assert line.greeks.gamma == pytest.approx(0.0, abs=1e-12)
