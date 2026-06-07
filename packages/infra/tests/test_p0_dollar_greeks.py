"""P0.2 — the $-Greek dollar layer: unit definitions, additivity, and the config flags.

Independent oracle: the dollar numbers are computed by hand here from the ADR-0036 unit
definitions (written in the comments), never read from the code under test. The two genuine
convention forks (gamma 1%-vs-$1, theta 365-vs-252) are driven by
:class:`MonetizationConfig`, and we assert each flag actually changes the output by the
exact pinned ratio — a flag that is inert would pass a sloppy test and fail this one.
"""

from __future__ import annotations

import pytest
from algotrading.core.config import MonetizationConfig
from algotrading.infra.pricing import dollar_greeks
from algotrading.infra.pricing.dollar_greeks import (
    dollar_delta,
    dollar_gamma,
    dollar_rho,
    dollar_theta,
    dollar_vega,
    gamma_unit_string,
    theta_unit_string,
)

# Hand fixture (Δ, Γ, Vega, Θ, Rho, S, mult, qty). The expected dollar numbers below are
# computed by hand from the ADR-0036 definitions, independent of the code under test.
DELTA = 0.5
GAMMA = 0.02
VEGA = 0.10
THETA = -7.3       # per year
RHO = 0.40
SPOT = 200.0
MULT = 100.0
QTY = 3.0

# By hand (per-contract, mult=100, then ×qty=3 for per-position):
#   Delta$ = Δ·S·mult       = 0.5 * 200 * 100        = 10_000  (per contract)
#   Gamma$ = Γ·S²/100·mult  = 0.02 * 40000 / 100 *100 =  800   (per contract, one_pct)
#   Vega$  = vega·0.01·mult  = 0.10 * 0.01 * 100      =    0.10 (per contract)
#   Theta$ = theta·mult/365  = -7.3 * 100 / 365       =   -2.0  (per contract, 365)
#   Rho$   = rho·0.01·mult   = 0.40 * 0.01 * 100      =    0.40 (per contract)
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
    # Position = contract × qty for every dollar number (the Phase-2 additivity invariant).
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
    # Risk-aggregation rule (TESTING): a 3-leg book's dollar delta equals the hand sum.
    legs = [
        (0.5, 100.0, 1.0, 2.0),   # (delta, spot, mult, qty)
        (-0.3, 100.0, 1.0, 5.0),
        (0.2, 100.0, 1.0, -1.0),
    ]
    by_hand = sum(d * s * m * q for (d, s, m, q) in legs)  # 0.5*100*2 + -0.3*100*5 + 0.2*100*-1
    # = 100 - 150 - 20 = -70
    assert by_hand == pytest.approx(-70.0, abs=TOL)
    total = sum(dollar_delta(d, s, m, q) for (d, s, m, q) in legs)
    assert total == pytest.approx(by_hand, abs=TOL)


def test_gamma_normalisation_flag_changes_the_dollar_number_by_exactly_100x() -> None:
    # one_pct (Γ·S²/100) vs one_dollar (Γ·S²): the ratio is exactly 100.
    one_pct = dollar_gamma(GAMMA, SPOT, MULT, normalisation="one_pct")
    one_dollar = dollar_gamma(GAMMA, SPOT, MULT, normalisation="one_dollar")
    assert one_dollar == pytest.approx(one_pct * 100.0, abs=1e-6)
    assert one_pct != pytest.approx(one_dollar)  # the flag is not inert


def test_theta_day_count_flag_changes_theta_by_the_day_count_ratio() -> None:
    # 365 -> 252 scales theta$ by 365/252 (a per-day number on fewer days is larger in mag).
    theta_365 = dollar_theta(THETA, MULT, day_count=365)
    theta_252 = dollar_theta(THETA, MULT, day_count=252)
    assert theta_252 == pytest.approx(theta_365 * (365.0 / 252.0), abs=1e-9)
    assert theta_252 != pytest.approx(theta_365)  # the flag is not inert


def test_dollar_greeks_reads_the_two_flags_from_the_config() -> None:
    default_cfg = MonetizationConfig(version="m")  # one_pct, 365
    one_dollar_cfg = MonetizationConfig(
        version="m", gamma_normalisation="one_dollar", theta_day_count=252
    )
    base = dict(delta=DELTA, gamma=GAMMA, vega=VEGA, theta=THETA, rho=RHO, spot=SPOT, multiplier=MULT)
    d1 = dollar_greeks(**base, config=default_cfg)
    d2 = dollar_greeks(**base, config=one_dollar_cfg)
    # gamma differs by ×100, theta by the day-count ratio; the flags drove the output.
    assert d2.dollar_gamma == pytest.approx(d1.dollar_gamma * 100.0, abs=1e-6)
    assert d2.dollar_theta == pytest.approx(d1.dollar_theta * (365.0 / 252.0), abs=1e-9)
    # The unit strings reflect the chosen convention.
    assert d1.gamma_unit == gamma_unit_string("one_pct") == "$ per 1% move"
    assert d1.theta_unit == theta_unit_string(365) == "$ per calendar day"
    assert d2.gamma_unit == "$ per $1 move"
    assert d2.theta_unit == "$ per trading day"
