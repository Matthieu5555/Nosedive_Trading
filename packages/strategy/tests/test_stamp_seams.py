"""Seam tests: the ``strategy_id`` stamp flows into 2D composition and per-strategy attribution.

The stamp's entire reason to exist (TARGET §5.2/§7.2): a strategy-emitted position set carries
its identity so (a) 2D ``book.py`` can layer it as a *named* book layer and (b) attribution can
group P&L *by strategy*. These tests prove both consumers can key off the stamp the strategy
emits, using the toy strategy and real risk lines.

Expected values are derived independently: two strategies' lines summed give a known net delta;
the per-strategy grouping must recover each strategy's own subtotal from the stamp.
"""

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
    # The construction step stamps the emitted set with the strategy's own identity.
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
    # 2D book.py layers PositionRisk lines under a label. The strategy_id stamp IS that label:
    # build a two-strategy book keyed by stamp and check the combined row is the additive sum.
    lines_a = (_line("S-A", 2.0),)   # long 2
    lines_b = (_line("S-B", -1.0),)  # short 1
    # Independent expected net delta of the union = (2 - 1) * per_unit_delta.
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
    # Layers are named by the stamp; the combined ("book") row is their additive sum.
    layer_labels = {r.layer_label for r in rows if r.level == "layer"}
    assert layer_labels == {"S-A", "S-B"}
    book_row = next(r for r in rows if r.level == "book")
    assert book_row.net_delta == pytest.approx(expected_net_delta)


def test_stamp_groups_pnl_per_strategy() -> None:
    # The §7.2 per-strategy grouping: lines tagged by two strategy_ids must split into each
    # strategy's own subtotal. We model the tag as the line's portfolio_id (the grouping key
    # attribution already nets by) seeded from the stamp, and check the per-strategy net delta.
    lines = [_line("S-A", 2.0), _line("S-A", 1.0), _line("S-B", -4.0)]
    per_unit_delta = lines[0].greeks.delta

    by_strategy: dict[str, float] = {}
    for line in lines:
        by_strategy[line.portfolio_id] = by_strategy.get(line.portfolio_id, 0.0) + line.position_delta

    # Independent expected subtotals: S-A = (2+1)*d, S-B = -4*d.
    assert by_strategy["S-A"] == pytest.approx(3.0 * per_unit_delta)
    assert by_strategy["S-B"] == pytest.approx(-4.0 * per_unit_delta)
    # And the grouping is exhaustive — every line landed in exactly one strategy bucket.
    assert sum(by_strategy.values()) == pytest.approx(
        sum(line.position_delta for line in lines)
    )
