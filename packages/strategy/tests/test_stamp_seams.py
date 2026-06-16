from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from algotrading.core.config import MonetizationConfig
from algotrading.core.provenance import ProvenanceStamp, source_ref, stamp
from algotrading.infra.risk import ContractValuationInput
from algotrading.infra.risk.book import BookLayerInput, build_book_greeks
from algotrading.infra.risk.greeks import PositionRisk, position_risk
from algotrading.strategy import (
    MarketState,
    SignalKind,
    StrategyContext,
    run_strategy,
    signal_snapshot,
)

from .reference_strategy import TOY_STRATEGY_ID, ToyStrategy

AS_OF = date(2026, 1, 5)
VALUATION_TS = datetime(2026, 1, 5, 16, 0, tzinfo=UTC)


def _line(portfolio_id: str, quantity: float) -> PositionRisk:
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
    return position_risk(portfolio_id=portfolio_id, quantity=quantity, valuation=valuation)


def _provenance() -> ProvenanceStamp:
    return stamp(
        calc_ts=VALUATION_TS,
        code_version="strategy-seam-test",
        config_hashes={"scenarios": "cfg-0"},
        source_records=(source_ref("market_state_snapshots", "seam-test"),),
        source_timestamps=(VALUATION_TS,),
    )


def test_strategy_emits_a_stamped_basket() -> None:
    step = run_strategy(
        ToyStrategy(),
        context=StrategyContext.BACKTEST,
        as_of=AS_OF,
        signals=signal_snapshot(AS_OF, {SignalKind.IMPLIED_CORRELATION: 0.9}),
        market=MarketState(as_of=AS_OF),
        basket_id="toy",
    )
    assert step.basket is not None
    assert step.basket.strategy_id == TOY_STRATEGY_ID


def test_stamp_keys_a_named_book_layer_in_2d_composition() -> None:
    lines_a = (_line("S-A", 2.0),)
    lines_b = (_line("S-B", -1.0),)
    per_unit_delta = lines_a[0].greeks.delta
    expected_net_delta = (2.0 - 1.0) * per_unit_delta

    rows = build_book_greeks(
        book_id="book-1",
        layers=[
            BookLayerInput(label="S-A", lines=lines_a),
            BookLayerInput(label="S-B", lines=lines_b),
        ],
        monetization=MonetizationConfig(version="strategy-seam-test"),
        valuation_ts=VALUATION_TS,
        source_snapshot_ts=VALUATION_TS,
        provenance=_provenance(),
    )
    layer_labels = {r.layer_label for r in rows if r.level == "layer"}
    assert layer_labels == {"S-A", "S-B"}
    book_row = next(r for r in rows if r.level == "book")
    assert book_row.net_delta == pytest.approx(expected_net_delta)


def test_stamp_groups_pnl_per_strategy() -> None:
    lines = [_line("S-A", 2.0), _line("S-A", 1.0), _line("S-B", -4.0)]
    per_unit_delta = lines[0].greeks.delta

    by_strategy: dict[str, float] = {}
    for line in lines:
        by_strategy[line.portfolio_id] = by_strategy.get(line.portfolio_id, 0.0) + line.position_delta

    assert by_strategy["S-A"] == pytest.approx(3.0 * per_unit_delta)
    assert by_strategy["S-B"] == pytest.approx(-4.0 * per_unit_delta)
    assert sum(by_strategy.values()) == pytest.approx(
        sum(line.position_delta for line in lines)
    )
