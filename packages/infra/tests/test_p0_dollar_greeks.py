from __future__ import annotations

import pytest
from algotrading.core.config import MonetizationConfig
from algotrading.infra.pricing import dollar_greeks
from algotrading.infra.pricing.dollar_greeks import (
    charm_unit_string,
    dollar_charm,
    dollar_delta,
    dollar_gamma,
    dollar_rho,
    dollar_rt_vega,
    dollar_theta,
    dollar_vanna,
    dollar_vega,
    dollar_volga,
    gamma_unit_string,
    theta_unit_string,
)

DELTA = 0.5
GAMMA = 0.02
VEGA = 0.10
THETA = -7.3
RHO = 0.40
SPOT = 200.0
MULT = 100.0
QTY = 3.0

DELTA_DOLLAR_PER_CONTRACT = 10_000.0
GAMMA_DOLLAR_PER_CONTRACT = 800.0
VEGA_DOLLAR_PER_CONTRACT = 0.10
THETA_DOLLAR_PER_CONTRACT = -2.0
RHO_DOLLAR_PER_CONTRACT = 0.40

TOL = 1e-9


def test_dollar_greek_unit_definitions_match_hand_values_per_contract() -> None:
    assert dollar_delta(DELTA, SPOT, MULT) == pytest.approx(DELTA_DOLLAR_PER_CONTRACT, abs=TOL)
    assert dollar_gamma(GAMMA, SPOT, MULT, normalisation="one_pct") == pytest.approx(
        GAMMA_DOLLAR_PER_CONTRACT, abs=TOL
    )
    assert dollar_vega(VEGA, MULT) == pytest.approx(VEGA_DOLLAR_PER_CONTRACT, abs=TOL)
    assert dollar_theta(THETA, MULT, day_count=365) == pytest.approx(
        THETA_DOLLAR_PER_CONTRACT, abs=TOL
    )
    assert dollar_rho(RHO, MULT) == pytest.approx(RHO_DOLLAR_PER_CONTRACT, abs=TOL)


def test_per_position_is_per_contract_times_quantity() -> None:
    assert dollar_delta(DELTA, SPOT, MULT, QTY) == pytest.approx(
        DELTA_DOLLAR_PER_CONTRACT * QTY, abs=TOL
    )
    assert dollar_gamma(GAMMA, SPOT, MULT, QTY, normalisation="one_pct") == pytest.approx(
        GAMMA_DOLLAR_PER_CONTRACT * QTY, abs=TOL
    )
    assert dollar_theta(THETA, MULT, QTY, day_count=365) == pytest.approx(
        THETA_DOLLAR_PER_CONTRACT * QTY, abs=TOL
    )


def test_book_dollar_delta_equals_the_hand_sum_over_a_three_leg_book() -> None:
    legs = [
        (0.5, 100.0, 1.0, 2.0),
        (-0.3, 100.0, 1.0, 5.0),
        (0.2, 100.0, 1.0, -1.0),
    ]
    by_hand = sum(d * s * m * q for (d, s, m, q) in legs)
    assert by_hand == pytest.approx(-70.0, abs=TOL)
    total = sum(dollar_delta(d, s, m, q) for (d, s, m, q) in legs)
    assert total == pytest.approx(by_hand, abs=TOL)


def test_gamma_normalisation_flag_changes_the_dollar_number_by_exactly_100x() -> None:
    one_pct = dollar_gamma(GAMMA, SPOT, MULT, normalisation="one_pct")
    one_dollar = dollar_gamma(GAMMA, SPOT, MULT, normalisation="one_dollar")
    assert one_dollar == pytest.approx(one_pct * 100.0, abs=1e-6)
    assert one_pct != pytest.approx(one_dollar)


def test_theta_day_count_flag_changes_theta_by_the_day_count_ratio() -> None:
    theta_365 = dollar_theta(THETA, MULT, day_count=365)
    theta_252 = dollar_theta(THETA, MULT, day_count=252)
    assert theta_252 == pytest.approx(theta_365 * (365.0 / 252.0), abs=1e-9)
    assert theta_252 != pytest.approx(theta_365)


VANNA = 0.05
VOLGA = 1.2
CHARM = -0.03
VANNA_DOLLAR_PER_CONTRACT = 10.0
VOLGA_DOLLAR_PER_CONTRACT = 0.012
CHARM_DOLLAR_PER_CONTRACT = -0.03 * 200.0 * 100.0 / 365.0


def test_second_order_dollar_unit_definitions_match_hand_values_per_contract() -> None:
    assert dollar_vanna(VANNA, SPOT, MULT) == pytest.approx(VANNA_DOLLAR_PER_CONTRACT, abs=TOL)
    assert dollar_volga(VOLGA, MULT) == pytest.approx(VOLGA_DOLLAR_PER_CONTRACT, abs=TOL)
    assert dollar_charm(CHARM, SPOT, MULT, day_count=365) == pytest.approx(
        CHARM_DOLLAR_PER_CONTRACT, abs=TOL
    )


def test_second_order_per_position_is_per_contract_times_quantity() -> None:
    assert dollar_vanna(VANNA, SPOT, MULT, QTY) == pytest.approx(
        VANNA_DOLLAR_PER_CONTRACT * QTY, abs=TOL
    )
    assert dollar_volga(VOLGA, MULT, QTY) == pytest.approx(VOLGA_DOLLAR_PER_CONTRACT * QTY, abs=TOL)
    assert dollar_charm(CHARM, SPOT, MULT, QTY, day_count=365) == pytest.approx(
        CHARM_DOLLAR_PER_CONTRACT * QTY, abs=TOL
    )


def test_charm_rides_the_theta_day_count_fork() -> None:
    charm_365 = dollar_charm(CHARM, SPOT, MULT, day_count=365)
    charm_252 = dollar_charm(CHARM, SPOT, MULT, day_count=252)
    assert charm_252 == pytest.approx(charm_365 * (365.0 / 252.0), abs=1e-9)
    assert charm_252 != pytest.approx(charm_365)


RT_VEGA = 0.18
RT_VEGA_DOLLAR_PER_CONTRACT = 0.18


def test_dollar_rt_vega_unit_definition_matches_hand_value() -> None:
    assert dollar_rt_vega(RT_VEGA, MULT) == pytest.approx(RT_VEGA_DOLLAR_PER_CONTRACT, abs=TOL)
    assert dollar_rt_vega(RT_VEGA, MULT, QTY) == pytest.approx(
        RT_VEGA_DOLLAR_PER_CONTRACT * QTY, abs=TOL
    )


def test_dollar_rt_vega_is_monetized_like_vega() -> None:
    assert dollar_rt_vega(VEGA, MULT, QTY) == pytest.approx(dollar_vega(VEGA, MULT, QTY), abs=TOL)


def test_dollar_greeks_monetizes_rt_vega_with_a_fixed_unforked_unit() -> None:
    cfg = MonetizationConfig(version="m")
    d = dollar_greeks(
        delta=DELTA, gamma=GAMMA, vega=VEGA, theta=THETA, rho=RHO, spot=SPOT, multiplier=MULT,
        rt_vega=RT_VEGA, config=cfg,
    )
    assert d.dollar_rt_vega == pytest.approx(RT_VEGA_DOLLAR_PER_CONTRACT, abs=TOL)
    cfg_alt = MonetizationConfig(
        version="m", gamma_normalisation="one_dollar", theta_day_count=252
    )
    d_alt = dollar_greeks(
        delta=DELTA, gamma=GAMMA, vega=VEGA, theta=THETA, rho=RHO, spot=SPOT, multiplier=MULT,
        rt_vega=RT_VEGA, config=cfg_alt,
    )
    assert d_alt.dollar_rt_vega == pytest.approx(d.dollar_rt_vega, abs=TOL)


def test_dollar_greeks_monetizes_the_second_order_set_and_forks_charm() -> None:
    default_cfg = MonetizationConfig(version="m")
    cfg_252 = MonetizationConfig(version="m", theta_day_count=252)
    base = dict(
        delta=DELTA, gamma=GAMMA, vega=VEGA, theta=THETA, rho=RHO, spot=SPOT, multiplier=MULT,
        vanna=VANNA, volga=VOLGA, charm=CHARM,
    )
    d1 = dollar_greeks(**base, config=default_cfg)
    assert d1.dollar_vanna == pytest.approx(VANNA_DOLLAR_PER_CONTRACT, abs=TOL)
    assert d1.dollar_volga == pytest.approx(VOLGA_DOLLAR_PER_CONTRACT, abs=TOL)
    assert d1.dollar_charm == pytest.approx(CHARM_DOLLAR_PER_CONTRACT, abs=TOL)
    assert d1.charm_unit == charm_unit_string(365) == "$ delta per calendar day"
    d2 = dollar_greeks(**base, config=cfg_252)
    assert d2.dollar_charm == pytest.approx(d1.dollar_charm * (365.0 / 252.0), abs=1e-9)
    assert d2.charm_unit == "$ delta per trading day"


def test_dollar_greeks_reads_the_two_flags_from_the_config() -> None:
    default_cfg = MonetizationConfig(version="m")
    one_dollar_cfg = MonetizationConfig(
        version="m", gamma_normalisation="one_dollar", theta_day_count=252
    )
    base = dict(delta=DELTA, gamma=GAMMA, vega=VEGA, theta=THETA, rho=RHO, spot=SPOT, multiplier=MULT)
    d1 = dollar_greeks(**base, config=default_cfg)
    d2 = dollar_greeks(**base, config=one_dollar_cfg)
    assert d2.dollar_gamma == pytest.approx(d1.dollar_gamma * 100.0, abs=1e-6)
    assert d2.dollar_theta == pytest.approx(d1.dollar_theta * (365.0 / 252.0), abs=1e-9)
    assert d1.gamma_unit == gamma_unit_string("one_pct") == "$ per 1% move"
    assert d1.theta_unit == theta_unit_string(365) == "$ per calendar day"
    assert d2.gamma_unit == "$ per $1 move"
    assert d2.theta_unit == "$ per trading day"
