"""Tests for the exit/kill decision and the band rebalance hook over real risk lines.

The decisions read the held :class:`PositionRisk` lines' ``position_delta`` (per-unit delta ×
multiplier × signed quantity). Expected net delta is derived independently from the line
inputs, then the toy's hand-checkable band rules (flatten/hedge when |net delta| > 0.25) are
applied to it — never read back from the strategy under test.
"""

from __future__ import annotations

from datetime import date

import pytest
from algotrading.infra.risk import ContractValuationInput
from algotrading.infra.risk.greeks import position_risk
from algotrading.strategy import ExitAction, MarketState

from .reference_strategy import TOY_DELTA_BAND, TOY_HEDGE_RATIO, ToyStrategy

AS_OF = date(2026, 1, 5)


def _call_line(quantity: float) -> object:
    # An ATM-ish European call: spot 100, K 100, T 0.25, vol 0.2 — the analytic delta is
    # positive (~0.5+ for a forward-ATM call), so a long lot has positive position delta and
    # a short lot negative. multiplier 1.0 keeps position_delta == per_unit_delta * quantity.
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
    # One long call has position_delta ≈ 0.54 (> band 0.25). To land INSIDE the band, pair a
    # tiny long + offsetting short so the net is near zero. Two opposite lots of the SAME line
    # net to exactly 0.0 delta, which is inside the 0.25 band.
    line_long = _call_line(1.0)
    line_short = _call_line(-1.0)
    net_delta = line_long.position_delta + line_short.position_delta  # type: ignore[attr-defined]
    assert net_delta == pytest.approx(0.0, abs=1e-12)  # independent: equal-and-opposite lots
    decision = ToyStrategy().decide_exit(
        MarketState(as_of=AS_OF, position_lines=(line_long, line_short))  # type: ignore[arg-type]
    )
    assert decision.action is ExitAction.HOLD


def test_large_net_delta_outside_band_flattens() -> None:
    # A single long call: position_delta is the call's analytic delta (> 0.5 here), which
    # exceeds the 0.25 band ⇒ the kill condition fires.
    line = _call_line(1.0)
    net_delta = line.position_delta  # type: ignore[attr-defined]
    assert net_delta > TOY_DELTA_BAND  # independent: a forward-ATM call delta > 0.25
    decision = ToyStrategy().decide_exit(
        MarketState(as_of=AS_OF, position_lines=(line,))  # type: ignore[arg-type]
    )
    assert decision.action is ExitAction.FLATTEN


def test_rebalance_hedges_the_breaching_delta() -> None:
    # Outside the band ⇒ hedge quantity = -net_delta (TOY_HEDGE_RATIO = -1.0): the trade that
    # neutralises the breach. Derived from the rule, applied to the independently-computed net.
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
