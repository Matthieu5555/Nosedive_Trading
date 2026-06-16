from __future__ import annotations

from datetime import date

import pytest
from algotrading.strategy import (
    EntryAction,
    MarketState,
    SignalKind,
    StrategyContext,
    UnstampedBasketError,
    run_strategy,
    signal_snapshot,
)
from algotrading.strategy.harness import StrategyStep

from .reference_strategy import TOY_ENTRY_THRESHOLD, TOY_STRATEGY_ID, ToyStrategy

AS_OF = date(2026, 1, 5)
ALL_CONTEXTS = tuple(StrategyContext)


def _step(context: StrategyContext, rho_bar: float) -> StrategyStep:
    strategy = ToyStrategy()
    signals = signal_snapshot(AS_OF, {SignalKind.IMPLIED_CORRELATION: rho_bar})
    market = MarketState(as_of=AS_OF, position_lines=())
    return run_strategy(
        strategy,
        context=context,
        as_of=AS_OF,
        signals=signals,
        market=market,
        basket_id="toy-basket",
    )


def test_same_instance_same_state_identical_across_four_contexts() -> None:
    rho_bar = 0.62
    steps = [_step(context, rho_bar) for context in ALL_CONTEXTS]
    decisions = {
        (s.entry, s.exit_, s.rebalance, s.basket, s.strategy_id, s.as_of) for s in steps
    }
    assert len(decisions) == 1, "the same strategy diverged across contexts"
    assert {s.context for s in steps} == set(ALL_CONTEXTS)


@pytest.mark.parametrize("context", ALL_CONTEXTS)
def test_entry_fires_above_threshold_and_constructs_a_stamped_basket(
    context: StrategyContext,
) -> None:
    step = _step(context, 0.62)
    assert step.entry.action is EntryAction.ENTER
    assert step.basket is not None
    assert step.basket.strategy_id == TOY_STRATEGY_ID
    assert len(step.basket.legs) == 2


@pytest.mark.parametrize("context", ALL_CONTEXTS)
def test_no_entry_below_threshold_constructs_nothing(context: StrategyContext) -> None:
    step = _step(context, 0.40)
    assert step.entry.action is EntryAction.NOOP
    assert step.basket is None


def test_missing_signal_holds_flat_never_fabricates() -> None:
    strategy = ToyStrategy()
    empty = signal_snapshot(AS_OF, {})
    step = run_strategy(
        strategy,
        context=StrategyContext.RESEARCH,
        as_of=AS_OF,
        signals=empty,
        market=MarketState(as_of=AS_OF),
        basket_id="b",
    )
    assert step.entry.action is EntryAction.NOOP
    assert step.basket is None


def test_threshold_is_strict_at_the_boundary() -> None:
    step = _step(StrategyContext.LIVE, TOY_ENTRY_THRESHOLD)
    assert step.entry.action is EntryAction.NOOP


def test_harness_rejects_a_mis_stamped_basket() -> None:
    class BadStamp(ToyStrategy):
        def construct(self, as_of: date, *, basket_id: str):  # type: ignore[override]
            good = super().construct(as_of, basket_id=basket_id)
            from dataclasses import replace

            return replace(good, strategy_id="WRONG")

    with pytest.raises(UnstampedBasketError) as exc:
        run_strategy(
            BadStamp(),
            context=StrategyContext.PAPER,
            as_of=AS_OF,
            signals=signal_snapshot(AS_OF, {SignalKind.IMPLIED_CORRELATION: 0.9}),
            market=MarketState(as_of=AS_OF),
            basket_id="b",
        )
    assert exc.value.expected == TOY_STRATEGY_ID
    assert exc.value.actual == "WRONG"
