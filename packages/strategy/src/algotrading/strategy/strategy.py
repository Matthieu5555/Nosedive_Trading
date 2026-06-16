from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Protocol, runtime_checkable

from algotrading.infra.contracts import Basket
from algotrading.infra.risk.greeks import PositionRisk

from .contract import StrategyContract
from .signals import SignalSnapshot


class EntryAction(StrEnum):

    ENTER = "enter"
    HOLD = "hold"
    NOOP = "noop"


class ExitAction(StrEnum):

    FLATTEN = "flatten"
    ROLL = "roll"
    HOLD = "hold"


@dataclass(frozen=True, slots=True)
class EntryDecision:

    action: EntryAction
    reason: str


@dataclass(frozen=True, slots=True)
class ExitDecision:

    action: ExitAction
    reason: str


@dataclass(frozen=True, slots=True)
class MarketState:

    as_of: date
    position_lines: tuple[PositionRisk, ...] = ()


@dataclass(frozen=True, slots=True)
class RebalanceDecision:

    hedge_quantity: float
    reason: str


@runtime_checkable
class Strategy(Protocol):

    @property
    def contract(self) -> StrategyContract:
        ...

    def decide_entry(self, as_of: date, signals: SignalSnapshot) -> EntryDecision:
        ...

    def decide_exit(self, market: MarketState) -> ExitDecision:
        ...

    def construct(self, as_of: date, *, basket_id: str) -> Basket:
        ...

    def rebalance(self, market: MarketState) -> RebalanceDecision:
        ...
