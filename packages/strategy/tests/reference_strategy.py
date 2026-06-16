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

TOY_STRATEGY_ID = "TOY"
TOY_ENTRY_THRESHOLD = 0.50
TOY_DELTA_BAND = 0.25
TOY_HEDGE_RATIO = -1.0


@dataclass(frozen=True, slots=True)
class ToyStrategy:

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
