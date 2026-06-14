"""The ``Strategy`` protocol — the minimal interface every S1–S5 object implements.

TARGET §6: "One logic, four contexts. Research, backtest, paper, and live call the same
strategy object." That object is defined here: a structural :class:`typing.Protocol` (so an
S-task implements it by shape, with no base-class coupling) exposing the four things the
spec asks for — its ``contract``, an **entry** decision from a signal input, an **exit/kill**
decision, and a **construction** step that emits a *stamped* 2A :class:`Basket` position set
— plus an optional band **rebalance** hook.

The protocol is the only home for the strategy interface; the harness (``harness.py``) is
the calling convention over it, the contract (``contract.py``) is the data it declares, and
the signal types (``signals.py``) are what its entry decision reads. No strategy *logic*
lives here — the S-tasks own construction/entry/exit rules; this fixes their shared shape.

**Layering:** this package imports infra/core only (it stamps the infra ``Basket`` and reads
the infra ``PositionRisk`` line). The kill switch that *enforces* an exit decision, and the
booker that turns the emitted basket into fills, live in the execution layer above — this
spine emits decisions and a position set; it does not enforce or book them.
"""

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
    """What an entry decision resolves to.

    ``ENTER`` opens the position the strategy would construct; ``HOLD`` keeps an open
    position with no new entry; ``NOOP`` is "the signal does not trigger and nothing is held"
    — the explicit do-nothing, distinct from HOLD so a context can tell "stayed flat" from
    "stayed in".
    """

    ENTER = "enter"
    HOLD = "hold"
    NOOP = "noop"


class ExitAction(StrEnum):
    """What an exit/kill decision resolves to.

    ``FLATTEN`` fires the declared ``kill_condition`` (close the position); ``ROLL`` rolls it
    forward (e.g. S2's daily roll, S5's calendar roll); ``HOLD`` keeps it open. The strategy
    *emits* this; the execution kill switch *enforces* a ``FLATTEN`` (cross-lane seam).
    """

    FLATTEN = "flatten"
    ROLL = "roll"
    HOLD = "hold"


@dataclass(frozen=True, slots=True)
class EntryDecision:
    """An entry decision: the action plus the human-readable reason it fired.

    ``reason`` traces the decision to the signal that drove it (e.g. "ρ̄=0.62 above
    entry threshold 0.55") — the audit trail attribution and the operator read. It is
    derived from the injected :class:`SignalSnapshot`, never from a clock or live read.
    """

    action: EntryAction
    reason: str


@dataclass(frozen=True, slots=True)
class ExitDecision:
    """An exit/kill decision: the action plus the reason, including a fired kill condition."""

    action: ExitAction
    reason: str


@dataclass(frozen=True, slots=True)
class MarketState:
    """The as-of market view a decision reads, injected by the calling context.

    ``as_of`` is the look-ahead anchor (a decision is a pure function of state at or before
    it). ``position_lines`` are the strategy's currently-held :class:`PositionRisk` lines (an
    empty tuple when flat) — the exit/kill and rebalance hooks read the live Greeks off these,
    the same already-priced lines the book and attribution consume, so the strategy never
    re-prices. The four contexts each populate this from their own source (a notebook's
    fixture, the backtester's replay, the paper/live snapshot) and the strategy reads it
    identically.
    """

    as_of: date
    position_lines: tuple[PositionRisk, ...] = ()


@dataclass(frozen=True, slots=True)
class RebalanceDecision:
    """An optional delta-hedge-band rebalance instruction emitted by :meth:`Strategy.rebalance`.

    ``hedge_quantity`` is the signed quantity of the hedge instrument (the underlying / index
    future leg) to trade to bring net delta back inside the band — positive to buy, negative
    to sell, ``0.0`` when inside the band (no trade). ``reason`` records the band breach that
    triggered it. The band rule itself is the shared ``strategy-delta-hedge-band`` rule the
    hook delegates to; this is the typed result the contexts act on.
    """

    hedge_quantity: float
    reason: str


@runtime_checkable
class Strategy(Protocol):
    """The interface every S1–S5 strategy object implements (TARGET §1/§3/§6).

    Structural — an implementor matches by having these members, no inheritance required.
    Every method is a **pure function of its injected arguments**: no clock, no live read,
    no store. The calling context (``harness.run_strategy``) supplies the state; the strategy
    returns decisions and a position set. That purity is exactly what makes "research ==
    backtest == paper == live" provable — the same instance fed the same state returns the
    same answer in all four contexts.
    """

    @property
    def contract(self) -> StrategyContract:
        """This strategy's frozen §1/§3 :class:`StrategyContract` (premium/signal/Greeks/kill)."""
        ...

    def decide_entry(self, as_of: date, signals: SignalSnapshot) -> EntryDecision:
        """Decide whether the signal triggers entry — ``(as_of, signals) → enter/hold/noop``.

        Reads the injected :class:`SignalSnapshot` (the infra signal layer's published
        outputs, never computed here) and the strategy's ``contract.signal``. Pure: same
        snapshot → same decision.
        """
        ...

    def decide_exit(self, market: MarketState) -> ExitDecision:
        """Decide whether to flatten/roll/hold — fires the declared ``kill_condition``.

        Reads the held :class:`PositionRisk` lines on ``market`` (the live Greeks) and
        returns the decision; the execution kill switch *enforces* a ``FLATTEN``. Pure.
        """
        ...

    def construct(self, as_of: date, *, basket_id: str) -> Basket:
        """Build the 2A :class:`Basket` position set the strategy would open, **stamped**.

        The returned basket carries ``strategy_id == self.contract.strategy_id`` so the same
        emitted set flows, named, into 2D composition (a book layer) and per-strategy
        attribution. Emits the leg container the whole book already speaks; the booker turns
        it into fills (execution lane, not here).
        """
        ...

    def rebalance(self, market: MarketState) -> RebalanceDecision:
        """Optional delta-hedge-band rebalance — delegates to the shared band rule.

        Returns a zero-quantity :class:`RebalanceDecision` for a strategy that does not
        hedge by band (the harness treats a zero quantity as "no trade"), so the hook is
        uniform across S1–S5 even though only the delta-neutral ones use it.
        """
        ...
