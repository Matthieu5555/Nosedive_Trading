from __future__ import annotations

import dataclasses
import math
from typing import Any

import pytest
from algotrading.core.config import MonetizationConfig
from algotrading.infra.pricing import DollarGreeks, dollar_greeks
from algotrading.infra.risk import (
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

ORACLE: dict[str, tuple[float, float, float, float, float]] = {
    "AAPL|OPT|C|100": (0.514739417780, 0.039445947495, 19.722973747691, -7.730479276483, 3.947883555998),  # noqa: E501
    "AAPL|OPT|P|100": (-0.475260582220, 0.039445947495, 19.722973747691, -7.730479276483, 3.947883555998),  # noqa: E501
    "AAPL|OPT|C|105": (0.327421504816, 0.035884390396, 17.942195198065, -7.094731500472, 2.043378946520),  # noqa: E501
    "AAPL|OPT|P|95": (-0.283872870388, 0.033707978743, 16.853989371359, -6.666452096311, 1.869182615580),  # noqa: E501
}

NET_DELTA = 850.59616033
NET_GAMMA = 30.48829087
NET_VEGA = 15244.14543326
NET_THETA = -5993.65908838

P1_DOLLAR_DELTA = 51473.94177800
P1_DOLLAR_GAMMA = 3944.59474954
P1_DOLLAR_VEGA = 197.22973748
P1_DOLLAR_THETA = -21.17939528


CONTRACTS = ["AAPL|OPT|C|100", "AAPL|OPT|P|100", "AAPL|OPT|C|105", "AAPL|OPT|P|95"]


def line_for(valuation: ContractValuationInput, quantity: float) -> PositionRisk:
    return position_risk(portfolio_id="pf-risk", quantity=quantity, valuation=valuation)


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
    call = line_for(CALL_100, 1.0).greeks
    put = line_for(PUT_100, 1.0).greeks
    assert 0.0 < call.delta < 1.0
    assert -1.0 < put.delta < 0.0
    assert call.gamma > 0.0 and put.gamma > 0.0
    assert call.vega > 0.0 and put.vega > 0.0
    assert call.theta < 0.0


@pytest.mark.parametrize("contract", CONTRACTS)
def test_analytic_and_central_difference_greeks_agree(contract: str) -> None:
    valuation = RISK_VALUATIONS[contract]
    analytic = line_for(valuation, 1.0).greeks
    fd = central_difference_greeks(valuation, bumps=DEFAULT_BUMPS)
    assert fd.delta == pytest.approx(analytic.delta, abs=1e-8)
    assert fd.gamma == pytest.approx(analytic.gamma, abs=1e-6)
    assert fd.vega == pytest.approx(analytic.vega, abs=1e-6)
    assert fd.theta == pytest.approx(analytic.theta, abs=1e-5)


_PINNED_MONETIZATION = MonetizationConfig(version="risk-test")


def dollar_greeks_for(line: PositionRisk, config: MonetizationConfig) -> DollarGreeks:
    g = line.greeks
    return dollar_greeks(
        delta=g.delta, gamma=g.gamma, vega=g.vega, theta=g.theta, rho=g.rho,
        spot=line.valuation.spot, multiplier=1.0, quantity=line.scale, config=config,
    )


def test_dollar_greeks_use_documented_conventions() -> None:
    line = line_for(CALL_100, 10.0)
    assert line.scale == pytest.approx(1000.0)
    d = dollar_greeks_for(line, _PINNED_MONETIZATION)
    assert d.dollar_delta == pytest.approx(P1_DOLLAR_DELTA, rel=1e-7)
    assert d.dollar_gamma == pytest.approx(P1_DOLLAR_GAMMA, rel=1e-7)
    assert d.dollar_vega == pytest.approx(P1_DOLLAR_VEGA, rel=1e-7)
    assert d.dollar_theta == pytest.approx(P1_DOLLAR_THETA, rel=1e-7)
    g = line.greeks
    assert d.dollar_delta == pytest.approx(g.delta * RISK_SPOT * 1000.0)
    assert d.dollar_gamma == pytest.approx(g.gamma * RISK_SPOT * RISK_SPOT / 100.0 * 1000.0)
    assert d.dollar_vega == pytest.approx(g.vega * 0.01 * 1000.0)
    assert d.dollar_theta == pytest.approx(g.theta / 365.0 * 1000.0)
    assert d.gamma_unit == "$ per 1% move"
    assert d.theta_unit == "$ per calendar day"


def test_dollar_gamma_and_vega_scale_exactly_with_multiplier() -> None:
    mult_1 = dataclasses.replace(CALL_100, multiplier=1.0)
    mult_100 = dataclasses.replace(CALL_100, multiplier=100.0)
    one = dollar_greeks_for(line_for(mult_1, 1.0), _PINNED_MONETIZATION)
    hundred = dollar_greeks_for(line_for(mult_100, 1.0), _PINNED_MONETIZATION)
    assert hundred.dollar_gamma == pytest.approx(100.0 * one.dollar_gamma)
    assert hundred.dollar_vega == pytest.approx(100.0 * one.dollar_vega)


def _all_lines() -> list[PositionRisk]:
    positions = risk_positions()
    return [line_for(RISK_VALUATIONS[p.contract_key], p.quantity) for p in positions]


def test_aggregate_equals_hand_summed_lines() -> None:
    lines = _all_lines()
    groups = aggregate_lines(lines, portfolio_id="pf-risk", dimension="underlying")
    assert len(groups) == 1
    net = groups[0]
    assert net.group_key == "underlying:AAPL"
    assert net.net_delta == pytest.approx(NET_DELTA, rel=1e-7)
    assert net.net_gamma == pytest.approx(NET_GAMMA, rel=1e-7)
    assert net.net_vega == pytest.approx(NET_VEGA, rel=1e-7)
    assert net.net_theta == pytest.approx(NET_THETA, rel=1e-7)
    assert net.net_delta == pytest.approx(sum(line.position_delta for line in lines))


def test_aggregate_by_instrument_and_maturity_partition_the_book() -> None:
    lines = _all_lines()
    by_instrument = aggregate_lines(lines, portfolio_id="pf-risk", dimension="instrument")
    assert {g.group_key for g in by_instrument} == {
        "instrument:AAPL|OPT|C|100",
        "instrument:AAPL|OPT|P|100",
        "instrument:AAPL|OPT|C|105",
    }
    assert sum(len(g.lines) for g in by_instrument) == len(lines)
    by_maturity = aggregate_lines(lines, portfolio_id="pf-risk", dimension="maturity")
    assert len(by_maturity) == 1
    assert by_maturity[0].group_key == "maturity:0.25"


def test_long_short_same_contract_nets_to_zero() -> None:
    lines = [line_for(CALL_100, 7.0), line_for(CALL_100, -7.0)]
    net = aggregate_lines(lines, portfolio_id="pf-risk", dimension="instrument")[0]
    assert net.net_delta == pytest.approx(0.0, abs=1e-12)
    assert net.net_gamma == pytest.approx(0.0, abs=1e-12)
    assert net.net_vega == pytest.approx(0.0, abs=1e-12)
    assert net.net_theta == pytest.approx(0.0, abs=1e-12)


def test_reconciliation_surfaces_a_breach_and_stays_quiet_within_threshold() -> None:
    line = line_for(CALL_100, 10.0)
    delta = line.greeks.delta
    breached = reconcile(line, BrokerGreeks(contract_key=line.contract_key, delta=delta + 0.01))
    assert [d.greek for d in breached] == ["delta"]
    assert breached[0].abs_diff == pytest.approx(0.01, abs=1e-9)
    within = reconcile(line, BrokerGreeks(contract_key=line.contract_key, delta=delta + 1e-4))
    assert within == []
    assert reconcile(line, BrokerGreeks(contract_key=line.contract_key)) == []


def test_empty_portfolio_aggregates_to_nothing() -> None:
    assert aggregate_lines([], portfolio_id="pf-risk", dimension="underlying") == []


def test_single_position_is_its_own_aggregate() -> None:
    lines = [line_for(CALL_100, 10.0)]
    net = aggregate_lines(lines, portfolio_id="pf-risk", dimension="underlying")[0]
    assert net.net_delta == pytest.approx(lines[0].position_delta)


def test_low_confidence_contract_is_priced_and_labelled_not_dropped() -> None:
    line = line_for(LOW_CONFIDENCE_CALL, 5.0)
    assert line.valuation.confidence == "low"
    assert line.greeks.price > 0.0
    net = aggregate_lines([line], portfolio_id="pf-risk", dimension="underlying")[0]
    assert net.lines[0].valuation.confidence == "low"


def test_multi_currency_aggregation_groups_by_desk() -> None:
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
    fd = central_difference_greeks(valuation, bumps=DEFAULT_BUMPS)
    assert fd.delta == pytest.approx(line.greeks.delta, abs=1e-8)
    assert fd.gamma == pytest.approx(line.greeks.gamma, abs=1e-6)
    assert fd.vega == pytest.approx(line.greeks.vega, abs=1e-6)
    assert fd.theta == pytest.approx(line.greeks.theta, abs=1e-5)
    assert line.scale == pytest.approx(200.0)
    d = dollar_greeks_for(line, _PINNED_MONETIZATION)
    assert d.dollar_gamma == pytest.approx(line.greeks.gamma * RISK_SPOT * RISK_SPOT / 100.0 * 200.0)


def test_reconciliation_surfaces_a_nan_broker_greek() -> None:
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
    with pytest.raises(ValuationError) as info:
        dataclasses.replace(CALL_100, **{field: value})  # type: ignore[arg-type]
    assert info.value.field == bad_field


def test_unknown_grouping_dimension_is_a_labeled_error() -> None:
    with pytest.raises(AggregationError):
        aggregate_lines(_all_lines(), portfolio_id="pf-risk", dimension="sector")


def test_degenerate_zero_maturity_prices_to_intrinsic_without_crashing() -> None:
    expired = dataclasses.replace(CALL_105, maturity_years=0.0, discount_factor=1.0)
    line = line_for(expired, 1.0)
    assert line.greeks.price == pytest.approx(max(RISK_SPOT - 105.0, 0.0), abs=1e-12)
    assert line.greeks.gamma == pytest.approx(0.0, abs=1e-12)


@pytest.mark.parametrize("bad_quantity", [math.nan, math.inf, -math.inf])
def test_position_risk_refuses_non_finite_quantity(bad_quantity: float) -> None:
    with pytest.raises(ValuationError) as info:
        position_risk(portfolio_id="pf-risk", quantity=bad_quantity, valuation=CALL_100)
    assert info.value.field == "quantity"
    assert info.value.reason == "must be a finite number"


def test_position_risk_accepts_a_negative_quantity() -> None:
    line = position_risk(portfolio_id="pf-risk", quantity=-3.0, valuation=CALL_100)
    assert line.scale == pytest.approx(CALL_100.multiplier * -3.0)


def test_position_risk_accepts_a_zero_quantity() -> None:
    line = position_risk(portfolio_id="pf-risk", quantity=0.0, valuation=CALL_100)
    assert line.scale == pytest.approx(0.0)


def test_basket_variance_raises_on_negative_variance_from_non_psd_correlation() -> None:
    from algotrading.infra.risk.basket import NonPSDBasketError, basket_variance

    weights = [1 / 3, 1 / 3, 1 / 3]
    vols = [1.0, 1.0, 1.0]
    with pytest.raises(NonPSDBasketError) as info:
        basket_variance(weights, vols, avg_correlation=-0.6)
    assert info.value.variance == pytest.approx(-0.0666666666667, abs=1e-9)
    assert info.value.variance < 0.0


def test_basket_variance_at_the_psd_boundary_is_zero_not_an_error() -> None:
    from algotrading.infra.risk.basket import basket_variance

    result = basket_variance([1 / 3, 1 / 3, 1 / 3], [1.0, 1.0, 1.0], avg_correlation=-0.5)
    assert result.variance == pytest.approx(0.0, abs=1e-12)
    assert result.vol == pytest.approx(0.0, abs=1e-12)


_SHUFFLES = [
    [0, 1, 2],
    [2, 1, 0],
    [1, 0, 2],
    [2, 0, 1],
    [0, 2, 1],
    [1, 2, 0],
]


def test_aggregate_net_greeks_invariant_under_input_shuffle() -> None:
    lines = _all_lines()
    ref = aggregate_lines(lines, portfolio_id="pf-risk", dimension="underlying")[0]

    for order in _SHUFFLES:
        shuffled = [lines[i] for i in order]
        groups = aggregate_lines(shuffled, portfolio_id="pf-risk", dimension="underlying")
        assert len(groups) == 1
        net = groups[0]
        assert net.net_delta == ref.net_delta
        assert net.net_gamma == ref.net_gamma
        assert net.net_vega == ref.net_vega
        assert net.net_theta == ref.net_theta

    assert ref.net_delta == pytest.approx(NET_DELTA, rel=1e-7)
    assert ref.net_gamma == pytest.approx(NET_GAMMA, rel=1e-7)
    assert ref.net_vega == pytest.approx(NET_VEGA, rel=1e-7)
    assert ref.net_theta == pytest.approx(NET_THETA, rel=1e-7)


def _build_scenario_cells_both_orders() -> tuple[list[Any], list[Any], Any]:
    from algotrading.infra.risk import (
        Scenario,
        ScenarioLinePnl,
        full_reprice_pnl,
    )

    scenario = Scenario(
        scenario_id="spot_+0.0500",
        family="spot",
        spot_shock=0.05,
        vol_shock=0.0,
        time_shock=0.0,
    )
    line_a = line_for(CALL_100, 5.0)
    line_b = line_for(PUT_100, 3.0)
    pnl_a = full_reprice_pnl(line_a, scenario)
    pnl_b = full_reprice_pnl(line_b, scenario)
    cell_a = ScenarioLinePnl(
        scenario=scenario, line=line_a,
        full_reprice_pnl=pnl_a, approx_pnl=pnl_a,
    )
    cell_b = ScenarioLinePnl(
        scenario=scenario, line=line_b,
        full_reprice_pnl=pnl_b, approx_pnl=pnl_b,
    )
    return [cell_a, cell_b], [cell_b, cell_a], scenario


def test_worst_by_underlying_total_invariant_under_cell_order() -> None:
    from algotrading.infra.risk import UnderlyingAttribution, WorstCase
    from algotrading.infra.risk.scenarios import _attribute_worst_by_underlying

    cells_fwd, cells_rev, scenario = _build_scenario_cells_both_orders()

    def _make_worst(cells: list) -> WorstCase:
        return WorstCase(
            scenario=scenario,
            total_pnl=math.fsum(c.full_reprice_pnl for c in cells),
            contributors=tuple(cells),
        )

    result_fwd: tuple[UnderlyingAttribution, ...] = _attribute_worst_by_underlying(_make_worst(cells_fwd))
    result_rev: tuple[UnderlyingAttribution, ...] = _attribute_worst_by_underlying(_make_worst(cells_rev))

    assert {u.underlying for u in result_fwd} == {u.underlying for u in result_rev}
    totals_fwd = {u.underlying: u.total_pnl for u in result_fwd}
    totals_rev = {u.underlying: u.total_pnl for u in result_rev}
    for underlying in totals_fwd:
        assert totals_fwd[underlying] == pytest.approx(totals_rev[underlying], rel=1e-15)


def test_attribute_by_family_total_invariant_under_cell_order() -> None:
    from algotrading.infra.risk.scenarios import _attribute_by_family

    cells_fwd, cells_rev, _ = _build_scenario_cells_both_orders()

    result_fwd = _attribute_by_family(cells_fwd)
    result_rev = _attribute_by_family(cells_rev)

    assert {f.family for f in result_fwd} == {f.family for f in result_rev}
    totals_fwd = {f.family: f.total_pnl for f in result_fwd}
    totals_rev = {f.family: f.total_pnl for f in result_rev}
    for family in totals_fwd:
        assert totals_fwd[family] == pytest.approx(totals_rev[family], rel=1e-15)

    oracle_spot_total = math.fsum(c.full_reprice_pnl for c in cells_fwd)
    assert totals_fwd["spot"] == pytest.approx(oracle_spot_total, rel=1e-15)


def test_risk_params_version_and_recon_version_are_independent() -> None:
    from algotrading.infra.risk import RiskParams

    section_both = {
        "grouping_keys": ["underlying"],
        "version": "risk-cfg-99",
        "recon_version": "recon-v7",
    }
    params = RiskParams.from_mapping(section_both)
    assert params.config_version == "risk-cfg-99"
    assert params.reconciliation_tolerance.version == "recon-v7"

    section_bump_cfg = {**section_both, "version": "risk-cfg-100"}
    params_bumped_cfg = RiskParams.from_mapping(section_bump_cfg)
    assert params_bumped_cfg.config_version == "risk-cfg-100"
    assert params_bumped_cfg.reconciliation_tolerance.version == "recon-v7"

    section_bump_recon = {**section_both, "recon_version": "recon-v8"}
    params_bumped_recon = RiskParams.from_mapping(section_bump_recon)
    assert params_bumped_recon.config_version == "risk-cfg-99"
    assert params_bumped_recon.reconciliation_tolerance.version == "recon-v8"


def test_risk_params_recon_version_defaults_independently_of_version() -> None:
    from algotrading.infra.risk import DEFAULT_RECON_TOLERANCE, RiskParams

    params_defaults = RiskParams.from_mapping({"grouping_keys": ["underlying"]})
    assert params_defaults.config_version == "risk-config-1.0.0"
    assert params_defaults.reconciliation_tolerance.version == DEFAULT_RECON_TOLERANCE.version

    params_cfg_only = RiskParams.from_mapping({
        "grouping_keys": ["underlying"],
        "version": "risk-cfg-42",
    })
    assert params_cfg_only.config_version == "risk-cfg-42"
    assert params_cfg_only.reconciliation_tolerance.version == DEFAULT_RECON_TOLERANCE.version

    params_recon_only = RiskParams.from_mapping({
        "grouping_keys": ["underlying"],
        "recon_version": "recon-v3",
    })
    assert params_recon_only.config_version == "risk-config-1.0.0"
    assert params_recon_only.reconciliation_tolerance.version == "recon-v3"
