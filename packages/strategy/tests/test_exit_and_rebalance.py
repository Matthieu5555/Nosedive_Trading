from __future__ import annotations

from datetime import date

import pytest
from algotrading.infra.risk import ContractValuationInput
from algotrading.infra.risk.greeks import position_risk
from algotrading.strategy import ExitAction, MarketState

from .reference_strategy import TOY_DELTA_BAND, TOY_HEDGE_RATIO, ToyStrategy

AS_OF = date(2026, 1, 5)


def _call_line(quantity: float) -> object:
    valuation = ContractValuationInput(
        contract_key="SX5E|OPT|C|100",
        underlying="SX5E",
        option_right="C",
        exercise_style="european",
        strike=100.0,
        maturity_years=0.25,
        spot=100.0,
        carry=0.0,
        volatility=0.20,
        discount_factor=0.99,
        multiplier=1.0,
        currency="EUR",
        confidence="ok",
    )
    return position_risk(portfolio_id="toy", quantity=quantity, valuation=valuation)


def test_flat_book_exit_holds() -> None:
    decision = ToyStrategy().decide_exit(MarketState(as_of=AS_OF, position_lines=()))
    assert decision.action is ExitAction.HOLD


def test_small_net_delta_inside_band_holds() -> None:
    line_long = _call_line(1.0)
    line_short = _call_line(-1.0)
    net_delta = line_long.position_delta + line_short.position_delta  # type: ignore[attr-defined]
    assert net_delta == pytest.approx(0.0, abs=1e-12)
    decision = ToyStrategy().decide_exit(
        MarketState(as_of=AS_OF, position_lines=(line_long, line_short))  # type: ignore[arg-type]
    )
    assert decision.action is ExitAction.HOLD


def test_large_net_delta_outside_band_flattens() -> None:
    line = _call_line(1.0)
    net_delta = line.position_delta  # type: ignore[attr-defined]
    assert net_delta > TOY_DELTA_BAND
    decision = ToyStrategy().decide_exit(
        MarketState(as_of=AS_OF, position_lines=(line,))  # type: ignore[arg-type]
    )
    assert decision.action is ExitAction.FLATTEN


def test_rebalance_hedges_the_breaching_delta() -> None:
    line = _call_line(1.0)
    net_delta = line.position_delta  # type: ignore[attr-defined]
    decision = ToyStrategy().rebalance(
        MarketState(as_of=AS_OF, position_lines=(line,))  # type: ignore[arg-type]
    )
    assert decision.hedge_quantity == pytest.approx(TOY_HEDGE_RATIO * net_delta)


def test_rebalance_inside_band_is_no_trade() -> None:
    line_long = _call_line(1.0)
    line_short = _call_line(-1.0)
    decision = ToyStrategy().rebalance(
        MarketState(as_of=AS_OF, position_lines=(line_long, line_short))  # type: ignore[arg-type]
    )
    assert decision.hedge_quantity == 0.0
