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

_SURFACE_PUT = "put"


@dataclass(frozen=True, slots=True)
class PutLineConfig:

    index: str
    put_tenor: str
    put_delta_band: str
    line_capacity: int
    contracts_per_day: float = 1.0
    max_rv_minus_iv: float = 0.0
    exit_delta_ceiling: float | None = None

    def __post_init__(self) -> None:
        if not self.index.strip():
            raise ValueError("PutLineConfig.index must be non-empty")
        if not self.put_tenor.strip():
            raise ValueError("PutLineConfig.put_tenor must be non-empty")
        if not self.put_delta_band.endswith("p"):
            raise ValueError(
                f"PutLineConfig.put_delta_band must be a put-wing band (end with 'p'), "
                f"got {self.put_delta_band!r}"
            )
        if self.line_capacity <= 0:
            raise ValueError(
                f"PutLineConfig.line_capacity must be positive, got {self.line_capacity}"
            )
        if self.contracts_per_day <= 0:
            raise ValueError(
                f"PutLineConfig.contracts_per_day must be positive, got {self.contracts_per_day}"
            )
        if self.exit_delta_ceiling is not None and self.exit_delta_ceiling <= 0:
            raise ValueError(
                f"PutLineConfig.exit_delta_ceiling must be positive when set, "
                f"got {self.exit_delta_ceiling}"
            )


@dataclass(frozen=True, slots=True)
class PutLineStrategy:

    config: PutLineConfig

    @property
    def contract(self) -> StrategyContract:
        return StrategyContract(
            strategy_id="S2-index-put-line",
            premium_harvested=(
                "index left-tail variance risk premium: index downside implied vol runs richer "
                "than realized, harvested as theta by a systematic short-put line"
            ),
            signal=SignalKind.IV_VS_REALIZED,
            intended_greeks=IntendedGreeks(
                delta=GreekSign.LONG,
                gamma=GreekSign.SHORT,
                vega=GreekSign.SHORT,
                theta=GreekSign.LONG,
            ),
            kill_condition=(
                "sharp sustained drawdown: spot falls through the put strikes and the short left "
                "tail hits (the line carries the loss); a vol-regime spike compounds it"
            ),
        )

    def decide_entry(self, as_of: date, signals: SignalSnapshot) -> EntryDecision:
        reading = signals.latest(
            SignalKind.IV_VS_REALIZED, subject=self.config.index
        ) or signals.latest(SignalKind.IV_VS_REALIZED)
        if reading is None:
            return EntryDecision(
                EntryAction.NOOP, "no IV-vs-realized reading; holding flat"
            )
        if reading.value <= self.config.max_rv_minus_iv:
            return EntryDecision(
                EntryAction.ENTER,
                f"RV-IV {reading.value} <= {self.config.max_rv_minus_iv}: index downside IV "
                f"rich vs realized, left-tail premium on offer",
            )
        return EntryDecision(
            EntryAction.NOOP,
            f"RV-IV {reading.value} above {self.config.max_rv_minus_iv}; implied not rich "
            f"enough vs realized, holding flat",
        )

    def line_at_capacity(self, open_contracts: float) -> bool:
        return open_contracts >= self.config.line_capacity

    def decide_sell(
        self, as_of: date, signals: SignalSnapshot, *, open_contracts: float
    ) -> EntryDecision:
        if self.line_at_capacity(open_contracts):
            return EntryDecision(
                EntryAction.NOOP,
                f"line at capacity ({open_contracts} >= {self.config.line_capacity}); "
                f"not selling",
            )
        return self.decide_entry(as_of, signals)

    def decide_exit(self, market: MarketState) -> ExitDecision:
        if self.config.exit_delta_ceiling is None:
            return ExitDecision(
                ExitAction.HOLD,
                "no position-side kill proxy configured; deferring flatten to the execution "
                "kill switch",
            )
        if not market.position_lines:
            return ExitDecision(ExitAction.HOLD, "flat; nothing to exit")
        net_delta = sum(line.position_delta for line in market.position_lines)
        if net_delta >= self.config.exit_delta_ceiling:
            return ExitDecision(
                ExitAction.FLATTEN,
                f"net delta {net_delta} at/above ceiling {self.config.exit_delta_ceiling}: "
                f"short puts going ITM, the left tail is hitting (drawdown kill)",
            )
        return ExitDecision(
            ExitAction.HOLD,
            f"net delta {net_delta} below ceiling {self.config.exit_delta_ceiling}; holding",
        )

    def construct(self, as_of: date, *, basket_id: str) -> Basket:
        put_leg = BasketLeg(
            instrument_kind="option",
            side="short",
            quantity=-self.config.contracts_per_day,
            underlying=self.config.index,
            tenor_label=self.config.put_tenor,
            delta_band=self.config.put_delta_band,
            surface_side=_SURFACE_PUT,
        )
        return Basket(
            basket_id=basket_id,
            trade_date=as_of,
            underlying=self.config.index,
            legs=(put_leg,),
            strategy_id=self.contract.strategy_id,
        )

    def rebalance(self, market: MarketState) -> RebalanceDecision:
        return RebalanceDecision(
            0.0, "S2 carries its short-put delta intentionally; no band hedge"
        )
