from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable

from algotrading.infra.contracts import Basket, BasketLeg

from .contract import GreekSign, IntendedGreeks, SignalKind, StrategyContract
from .delta_hedge_band import DeltaHedgeBand, decide_delta_hedge
from .signals import SignalSnapshot
from .strategy import (
    EntryAction,
    EntryDecision,
    ExitAction,
    ExitDecision,
    MarketState,
    RebalanceDecision,
)

_ATM_CALL_BAND = "atm"
_SURFACE_CALL = "call"


class GammaConstructionError(ValueError):

    def __init__(self, as_of: date, reason: str) -> None:
        self.as_of = as_of
        self.reason = reason
        super().__init__(f"S3 gamma construct failed as of {as_of}: {reason}")


@dataclass(frozen=True, slots=True)
class GammaConfig:

    index: str
    option_tenor: str
    entry_iv_rank_max: float
    contracts: float = 1.0
    exit_gamma_floor: float = 0.0
    delta_band: float = 0.0
    min_hedge_units: float = 1e-6

    def __post_init__(self) -> None:
        if not self.index.strip():
            raise ValueError("GammaConfig.index must be non-empty")
        if not self.option_tenor.strip():
            raise ValueError("GammaConfig.option_tenor must be non-empty")
        if self.contracts <= 0:
            raise ValueError(
                f"GammaConfig.contracts must be positive, got {self.contracts}"
            )
        if self.delta_band < 0:
            raise ValueError(
                f"GammaConfig.delta_band must be non-negative, got {self.delta_band}"
            )
        if self.min_hedge_units < 0:
            raise ValueError(
                f"GammaConfig.min_hedge_units must be non-negative, got {self.min_hedge_units}"
            )


@runtime_checkable
class GammaMarketData(Protocol):

    def cheapest_name(self, as_of: date) -> str | None:
        ...

    def net_dollar_delta(self, legs: Sequence[BasketLeg], as_of: date) -> float | None:
        ...

    def share_unit_dollar_delta(self, name: str, as_of: date) -> float | None:
        ...


def _leg_side(quantity: float) -> str:
    return "long" if quantity > 0 else "short"


@dataclass(frozen=True, slots=True)
class GammaStrategy:

    config: GammaConfig
    data: GammaMarketData

    @property
    def contract(self) -> StrategyContract:
        return StrategyContract(
            strategy_id="S3-gamma",
            premium_harvested=(
                "gamma premium: realized vol exceeds implied on one cheap name; a long-gamma "
                "delta-neutral structure scalps the difference (each delta-band round trip "
                "banks the realized-vol rectangle)"
            ),
            signal=SignalKind.IV_RANK,
            intended_greeks=IntendedGreeks(
                delta=GreekSign.FLAT,
                gamma=GreekSign.LONG,
                vega=GreekSign.LONG,
                theta=GreekSign.SHORT,
            ),
            kill_condition=(
                "quiet drift + IV crush: realized vol stays below implied so the scalp gains "
                "fall short of theta while the long-vol structure bleeds (gain < theta)"
            ),
        )

    def decide_entry(self, as_of: date, signals: SignalSnapshot) -> EntryDecision:
        readings = signals.all_of(SignalKind.IV_RANK)
        if not readings:
            return EntryDecision(
                EntryAction.NOOP, "no IV-rank reading; holding flat"
            )
        cheapest = min(readings, key=lambda r: r.value)
        if cheapest.value <= self.config.entry_iv_rank_max:
            return EntryDecision(
                EntryAction.ENTER,
                f"cheapest name {cheapest.subject!r} IV rank {cheapest.value} <= entry "
                f"{self.config.entry_iv_rank_max}: vol cheap, long-gamma scalp available",
            )
        return EntryDecision(
            EntryAction.NOOP,
            f"cheapest name {cheapest.subject!r} IV rank {cheapest.value} above entry "
            f"{self.config.entry_iv_rank_max}; no cheap vol, holding flat",
        )

    def decide_exit(self, market: MarketState) -> ExitDecision:
        if not market.position_lines:
            return ExitDecision(ExitAction.HOLD, "flat; nothing to exit")
        net_gamma = sum(line.position_gamma for line in market.position_lines)
        if net_gamma <= self.config.exit_gamma_floor:
            return ExitDecision(
                ExitAction.FLATTEN,
                f"net gamma {net_gamma} at/below floor {self.config.exit_gamma_floor}: "
                f"long-gamma thesis gone (no rectangle to scalp, only theta bleed)",
            )
        return ExitDecision(
            ExitAction.HOLD,
            f"net gamma {net_gamma} above floor {self.config.exit_gamma_floor}; holding",
        )

    def construct(self, as_of: date, *, basket_id: str) -> Basket:
        name = self.data.cheapest_name(as_of)
        if name is None:
            raise GammaConstructionError(
                as_of, f"no cheap name (per-name IV rank) resolved for index {self.config.index!r}"
            )

        call_leg = self._call_leg(name)
        stock_legs = self._stock_hedge_legs(name, call_leg, as_of)

        return Basket(
            basket_id=basket_id,
            trade_date=as_of,
            underlying=name,
            legs=(call_leg, *stock_legs),
            strategy_id=self.contract.strategy_id,
        )

    def rebalance(self, market: MarketState) -> RebalanceDecision:
        if not market.position_lines:
            return RebalanceDecision(0.0, "flat; no delta to hedge")
        net_delta = sum(line.position_delta for line in market.position_lines)
        band = DeltaHedgeBand(target=0.0, half_width=self.config.delta_band)
        instruction = decide_delta_hedge(net_delta, band)
        return RebalanceDecision(instruction.hedge_quantity, instruction.reason)

    def _call_leg(self, name: str) -> BasketLeg:
        return BasketLeg(
            instrument_kind="option",
            side="long",
            quantity=self.config.contracts,
            underlying=name,
            tenor_label=self.config.option_tenor,
            delta_band=_ATM_CALL_BAND,
            surface_side=_SURFACE_CALL,
        )

    def _stock_hedge_legs(
        self, name: str, call_leg: BasketLeg, as_of: date
    ) -> tuple[BasketLeg, ...]:
        call_delta = self.data.net_dollar_delta((call_leg,), as_of)
        if call_delta is None:
            raise GammaConstructionError(
                as_of, "grid could not supply the long call's net dollar delta to hedge"
            )
        share_unit = self.data.share_unit_dollar_delta(name, as_of)
        if share_unit is None:
            raise GammaConstructionError(
                as_of, f"no spot resolved for {name!r}; cannot size the stock hedge"
            )
        if share_unit == 0:
            raise GammaConstructionError(
                as_of, f"{name!r} has zero unit dollar delta (spot 0); cannot size a hedge"
            )

        shares = -call_delta / share_unit
        if abs(shares) < self.config.min_hedge_units:
            return ()

        return (
            BasketLeg(
                instrument_kind="stock",
                side=_leg_side(shares),
                quantity=shares,
                underlying=name,
            ),
        )
