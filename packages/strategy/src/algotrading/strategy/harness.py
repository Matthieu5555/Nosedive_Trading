from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from algotrading.infra.contracts import Basket

from .signals import SignalSnapshot
from .strategy import (
    EntryAction,
    EntryDecision,
    ExitDecision,
    MarketState,
    RebalanceDecision,
    Strategy,
)


class StrategyContext(StrEnum):

    RESEARCH = "research"
    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"


@dataclass(frozen=True, slots=True)
class StrategyStep:

    context: StrategyContext
    as_of: date
    strategy_id: str
    entry: EntryDecision
    exit_: ExitDecision
    rebalance: RebalanceDecision
    basket: Basket | None


def run_strategy(
    strategy: Strategy,
    *,
    context: StrategyContext,
    as_of: date,
    signals: SignalSnapshot,
    market: MarketState,
    basket_id: str,
) -> StrategyStep:
    entry = strategy.decide_entry(as_of, signals)
    exit_ = strategy.decide_exit(market)
    rebalance = strategy.rebalance(market)
    basket = strategy.construct(as_of, basket_id=basket_id) if entry.action is EntryAction.ENTER \
        else None
    if basket is not None and basket.strategy_id != strategy.contract.strategy_id:
        raise UnstampedBasketError(
            strategy.contract.strategy_id, basket.strategy_id
        )
    return StrategyStep(
        context=context,
        as_of=as_of,
        strategy_id=strategy.contract.strategy_id,
        entry=entry,
        exit_=exit_,
        rebalance=rebalance,
        basket=basket,
    )


class UnstampedBasketError(ValueError):

    def __init__(self, expected: str, actual: str | None) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"construct() emitted a basket stamped {actual!r}, expected the strategy's own "
            f"strategy_id {expected!r} — an unstamped set cannot be grouped by strategy"
        )
