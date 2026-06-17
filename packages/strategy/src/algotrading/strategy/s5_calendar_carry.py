from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from algotrading.infra.contracts import Basket, BasketLeg

from .contract import GreekSign, IntendedGreeks, SignalKind, StrategyContract
from .signals import SignalSnapshot
from .strategy import (
    EntryAction,
    EntryDecision,
    ExitAction,
    ExitDecision,
    MarketState,
    RebalanceDecision,
)

_SURFACE_CALL = "call"


@dataclass(frozen=True, slots=True)
class CalendarCarryConfig:

    index: str
    front_tenor: str
    back_tenor: str
    strike_band: str
    entry_slope_threshold: float
    contracts: float = 1.0
    surface_side: str = _SURFACE_CALL
    exit_theta_floor: float | None = None

    def __post_init__(self) -> None:
        if not self.index.strip():
            raise ValueError("CalendarCarryConfig.index must be non-empty")
        if not self.front_tenor.strip():
            raise ValueError("CalendarCarryConfig.front_tenor must be non-empty")
        if not self.back_tenor.strip():
            raise ValueError("CalendarCarryConfig.back_tenor must be non-empty")
        if self.front_tenor == self.back_tenor:
            raise ValueError(
                f"CalendarCarryConfig front_tenor and back_tenor must differ, "
                f"got both {self.front_tenor!r}"
            )
        if not self.strike_band.strip():
            raise ValueError("CalendarCarryConfig.strike_band must be non-empty")
        if self.contracts <= 0:
            raise ValueError(
                f"CalendarCarryConfig.contracts must be positive, got {self.contracts}"
            )


@dataclass(frozen=True, slots=True)
class CalendarCarryStrategy:

    config: CalendarCarryConfig

    @property
    def contract(self) -> StrategyContract:
        return StrategyContract(
            strategy_id="S5-calendar-carry",
            premium_harvested=(
                "term-structure carry: in contango the front month decays faster than the "
                "back at the same strike, so a short-front / long-back calendar banks the "
                "theta differential while the long back carries the vega"
            ),
            signal=SignalKind.TERM_STRUCTURE_SLOPE,
            intended_greeks=IntendedGreeks(
                delta=GreekSign.FLAT,
                gamma=GreekSign.SHORT,
                vega=GreekSign.LONG,
                theta=GreekSign.LONG,
            ),
            kill_condition=(
                "front-month event repricing inverts the term structure: the front bid up "
                "above the back, the carry reverses and the short front leg carries the loss"
            ),
        )

    def decide_entry(self, as_of: date, signals: SignalSnapshot) -> EntryDecision:
        reading = signals.latest(
            SignalKind.TERM_STRUCTURE_SLOPE, subject=self.config.index
        ) or signals.latest(SignalKind.TERM_STRUCTURE_SLOPE)
        if reading is None:
            return EntryDecision(
                EntryAction.NOOP, "no term-structure slope reading; holding flat"
            )
        if reading.value >= self.config.entry_slope_threshold:
            return EntryDecision(
                EntryAction.ENTER,
                f"term slope {reading.value} >= entry {self.config.entry_slope_threshold}: "
                f"contango, front decays faster than back, calendar carry on offer",
            )
        return EntryDecision(
            EntryAction.NOOP,
            f"term slope {reading.value} below entry {self.config.entry_slope_threshold}; "
            f"term structure not steep enough, holding flat",
        )

    def decide_exit(self, market: MarketState) -> ExitDecision:
        if self.config.exit_theta_floor is None:
            return ExitDecision(
                ExitAction.HOLD,
                "no position-side kill proxy configured; deferring flatten to the execution "
                "kill switch",
            )
        if not market.position_lines:
            return ExitDecision(ExitAction.HOLD, "flat; nothing to exit")
        net_theta = sum(line.position_theta for line in market.position_lines)
        if net_theta <= self.config.exit_theta_floor:
            return ExitDecision(
                ExitAction.FLATTEN,
                f"net theta {net_theta} at/below floor {self.config.exit_theta_floor}: the "
                f"front decay no longer outpaces the back (term structure inverting, "
                f"front-month event repricing) — carry reversed, kill",
            )
        return ExitDecision(
            ExitAction.HOLD,
            f"net theta {net_theta} above floor {self.config.exit_theta_floor}; holding",
        )

    def construct(self, as_of: date, *, basket_id: str) -> Basket:
        front_leg = BasketLeg(
            instrument_kind="option",
            side="short",
            quantity=-self.config.contracts,
            underlying=self.config.index,
            tenor_label=self.config.front_tenor,
            delta_band=self.config.strike_band,
            surface_side=self.config.surface_side,
        )
        back_leg = BasketLeg(
            instrument_kind="option",
            side="long",
            quantity=self.config.contracts,
            underlying=self.config.index,
            tenor_label=self.config.back_tenor,
            delta_band=self.config.strike_band,
            surface_side=self.config.surface_side,
        )
        return Basket(
            basket_id=basket_id,
            trade_date=as_of,
            underlying=self.config.index,
            legs=(front_leg, back_leg),
            strategy_id=self.contract.strategy_id,
        )

    def rebalance(self, market: MarketState) -> RebalanceDecision:
        return RebalanceDecision(
            0.0,
            "S5 holds its same-strike calendar; the two legs net near delta-flat, no band hedge",
        )
