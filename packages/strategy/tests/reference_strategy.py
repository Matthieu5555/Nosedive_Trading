"""A trivial toy ``Strategy`` (NOT S1–S5) used to prove the spine.

The done-criteria fixture: a deliberately minimal strategy whose only job is to exercise the
protocol — one signal threshold, one two-leg basket, a band rebalance and a kill rule simple
enough to hand-check. It exists so the harness, the stamp seam, the book composition, and the
attribution grouping can be tested against a *real* implementor without dragging in any of the
real strategies' economics (which the S-tasks own).

It implements :class:`~algotrading.strategy.Strategy` structurally (no inheritance), which is
itself part of what the tests check: the protocol is satisfiable by shape alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from algotrading.infra.contracts import Basket, BasketLeg
from algotrading.strategy import (
    EntryAction,
    EntryDecision,
    ExitAction,
    ExitDecision,
    GreekSign,
    IntendedGreeks,
    MarketState,
    RebalanceDecision,
    SignalKind,
    StrategyContract,
)

# The toy's hand-checkable rules — all internal invariants of the fixture, not business config.
TOY_STRATEGY_ID = "TOY"
TOY_ENTRY_THRESHOLD = 0.50  # enter when the implied-correlation reading exceeds this
TOY_DELTA_BAND = 0.25  # flatten when |net delta| breaches this band (kill rule)
TOY_HEDGE_RATIO = -1.0  # hedge quantity = -net_delta * ratio (a unit delta-neutralising hedge)


@dataclass(frozen=True, slots=True)
class ToyStrategy:
    """A minimal, hand-checkable strategy: enter on ρ̄ > 0.5, flatten on a delta-band breach."""

    @property
    def contract(self) -> StrategyContract:
        return StrategyContract(
            strategy_id=TOY_STRATEGY_ID,
            premium_harvested="toy correlation premium (fixture)",
            signal=SignalKind.IMPLIED_CORRELATION,
            intended_greeks=IntendedGreeks(
                delta=GreekSign.FLAT,
                gamma=GreekSign.LONG,
                vega=GreekSign.LONG,
                theta=GreekSign.SHORT,
            ),
            kill_condition="net delta drifts outside the hedge band (fixture)",
        )

    def decide_entry(self, as_of: date, signals: object) -> EntryDecision:
        # `signals` is a SignalSnapshot; typed loosely here so the fixture stays dependency-light.
        reading = signals.latest(SignalKind.IMPLIED_CORRELATION)  # type: ignore[attr-defined]
        if reading is None:
            return EntryDecision(EntryAction.NOOP, "no implied-correlation reading; holding flat")
        if reading.value > TOY_ENTRY_THRESHOLD:
            return EntryDecision(
                EntryAction.ENTER, f"rho_bar {reading.value} above entry {TOY_ENTRY_THRESHOLD}"
            )
        return EntryDecision(
            EntryAction.NOOP, f"rho_bar {reading.value} not above entry {TOY_ENTRY_THRESHOLD}"
        )

    def decide_exit(self, market: MarketState) -> ExitDecision:
        net_delta = sum(line.position_delta for line in market.position_lines)
        if not market.position_lines:
            return ExitDecision(ExitAction.HOLD, "flat; nothing to exit")
        if abs(net_delta) > TOY_DELTA_BAND:
            return ExitDecision(
                ExitAction.FLATTEN, f"net delta {net_delta} outside band {TOY_DELTA_BAND}"
            )
        return ExitDecision(ExitAction.HOLD, f"net delta {net_delta} inside band {TOY_DELTA_BAND}")

    def construct(self, as_of: date, *, basket_id: str) -> Basket:
        # A two-leg toy: long one call grid cell, short the underlying — stamped with the identity.
        legs = (
            BasketLeg(
                instrument_kind="option", side="long", quantity=1.0,
                underlying="SX5E", tenor_label="1M", delta_band="25D",
            ),
            BasketLeg(
                instrument_kind="stock", side="short", quantity=-1.0, underlying="SX5E",
            ),
        )
        return Basket(
            basket_id=basket_id,
            trade_date=as_of,
            underlying="SX5E",
            legs=legs,
            strategy_id=self.contract.strategy_id,
        )

    def rebalance(self, market: MarketState) -> RebalanceDecision:
        net_delta = sum(line.position_delta for line in market.position_lines)
        if abs(net_delta) <= TOY_DELTA_BAND:
            return RebalanceDecision(0.0, f"net delta {net_delta} inside band; no hedge")
        return RebalanceDecision(
            TOY_HEDGE_RATIO * net_delta, f"net delta {net_delta} breached band; hedging"
        )
