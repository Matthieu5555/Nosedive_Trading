from __future__ import annotations

import random

import pytest
from algotrading.infra.risk import ContractValuationInput, aggregate_lines, position_risk
from hypothesis import given
from hypothesis import strategies as st

_RIGHTS = ("C", "P")


@st.composite
def portfolios(draw: st.DrawFn) -> list:
    n = draw(st.integers(min_value=1, max_value=6))
    strikes = draw(
        st.lists(
            st.floats(min_value=60.0, max_value=140.0),
            min_size=n,
            max_size=n,
            unique_by=lambda s: round(s, 4),
        )
    )
    lines = []
    for strike in strikes:
        right = draw(st.sampled_from(_RIGHTS))
        quantity = draw(st.floats(min_value=-50.0, max_value=50.0, allow_nan=False))
        volatility = draw(st.floats(min_value=0.05, max_value=0.80, allow_nan=False))
        valuation = ContractValuationInput(
            contract_key=f"AAPL|OPT|{right}|{strike:.4f}",
            underlying="AAPL",
            option_right=right,
            exercise_style="european",
            strike=strike,
            maturity_years=0.25,
            spot=100.0,
            carry=0.0,
            volatility=volatility,
            discount_factor=0.99,
            multiplier=100.0,
            currency="USD",
        )
        lines.append(
            position_risk(portfolio_id="pf", quantity=quantity, valuation=valuation)
        )
    return lines


@given(data=portfolios(), perm_seed=st.randoms(use_true_random=False))
def test_aggregate_is_invariant_under_reordering(data: list, perm_seed: random.Random) -> None:
    shuffled = list(data)
    perm_seed.shuffle(shuffled)
    base = aggregate_lines(data, portfolio_id="pf", dimension="underlying")[0]
    other = aggregate_lines(shuffled, portfolio_id="pf", dimension="underlying")[0]
    assert other.net_delta == base.net_delta
    assert other.net_gamma == base.net_gamma
    assert other.net_vega == base.net_vega
    assert other.net_theta == base.net_theta


@given(data=portfolios())
def test_sum_of_lines_equals_the_aggregate(data: list) -> None:
    groups = aggregate_lines(data, portfolio_id="pf", dimension="underlying")
    assert len(groups) == 1
    net = groups[0]
    assert net.net_delta == pytest.approx(
        sum(line.position_delta for line in data), rel=1e-9, abs=1e-9
    )
    assert net.net_gamma == pytest.approx(
        sum(line.position_gamma for line in data), rel=1e-9, abs=1e-9
    )
    assert net.net_vega == pytest.approx(
        sum(line.position_vega for line in data), rel=1e-9, abs=1e-9
    )
    assert net.net_theta == pytest.approx(
        sum(line.position_theta for line in data), rel=1e-9, abs=1e-9
    )
