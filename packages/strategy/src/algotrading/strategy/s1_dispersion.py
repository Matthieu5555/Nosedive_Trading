from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable

from algotrading.infra.contracts import (
    SURFACE_SIDE_COMBINED,
    Basket,
    BasketLeg,
)
from algotrading.infra.universe import BasketMember

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
_ATM_PUT_BAND = "atmp"
_SURFACE_CALL = "call"
_SURFACE_PUT = "put"


class DispersionConstructionError(ValueError):

    def __init__(self, as_of: date, reason: str) -> None:
        self.as_of = as_of
        self.reason = reason
        super().__init__(f"S1 dispersion construct failed as of {as_of}: {reason}")


@dataclass(frozen=True, slots=True)
class DispersionConfig:

    index: str
    top_n: int
    straddle_tenor: str
    entry_threshold: float
    contracts_per_name: float = 1.0
    exit_vega_floor: float = 0.0
    delta_band: float = 0.0
    min_hedge_units: float = 1e-6

    def __post_init__(self) -> None:
        if not self.index.strip():
            raise ValueError("DispersionConfig.index must be non-empty")
        if self.top_n <= 0:
            raise ValueError(f"DispersionConfig.top_n must be positive, got {self.top_n}")
        if not self.straddle_tenor.strip():
            raise ValueError("DispersionConfig.straddle_tenor must be non-empty")
        if self.contracts_per_name <= 0:
            raise ValueError(
                f"DispersionConfig.contracts_per_name must be positive, "
                f"got {self.contracts_per_name}"
            )
        if self.delta_band < 0:
            raise ValueError(
                f"DispersionConfig.delta_band must be non-negative, got {self.delta_band}"
            )
        if self.min_hedge_units < 0:
            raise ValueError(
                f"DispersionConfig.min_hedge_units must be non-negative, "
                f"got {self.min_hedge_units}"
            )


@runtime_checkable
class DispersionMarketData(Protocol):

    def top_n_members(self, as_of: date) -> tuple[BasketMember, ...]:
        ...

    def net_dollar_delta(self, legs: Sequence[BasketLeg], as_of: date) -> float | None:
        ...

    def forward_unit_dollar_delta(self, as_of: date) -> float | None:
        ...


def _leg_side(quantity: float) -> str:
    return "long" if quantity > 0 else "short"


@dataclass(frozen=True, slots=True)
class DispersionStrategy:

    config: DispersionConfig
    data: DispersionMarketData

    @property
    def contract(self) -> StrategyContract:
        return StrategyContract(
            strategy_id="S1-dispersion",
            premium_harvested=(
                "correlation premium: index ATM IV rich vs the constituent ATM IVs on the "
                "same tenor (high implied correlation ρ̄)"
            ),
            signal=SignalKind.IMPLIED_CORRELATION,
            intended_greeks=IntendedGreeks(
                delta=GreekSign.FLAT,
                gamma=GreekSign.LONG,
                vega=GreekSign.LONG,
                theta=GreekSign.SHORT,
            ),
            kill_condition=(
                "the names re-correlate: realized correlation rises and single-name vol "
                "falls together while the long-vol book bleeds theta"
            ),
        )

    def decide_entry(self, as_of: date, signals: SignalSnapshot) -> EntryDecision:
        reading = signals.latest(
            SignalKind.IMPLIED_CORRELATION, subject=self.config.index
        ) or signals.latest(SignalKind.IMPLIED_CORRELATION)
        if reading is None:
            return EntryDecision(
                EntryAction.NOOP, "no implied-correlation reading; holding flat"
            )
        if reading.value >= self.config.entry_threshold:
            return EntryDecision(
                EntryAction.ENTER,
                f"rho_bar {reading.value} >= entry {self.config.entry_threshold}: index IV "
                f"rich vs constituents, correlation premium available",
            )
        return EntryDecision(
            EntryAction.NOOP,
            f"rho_bar {reading.value} below entry {self.config.entry_threshold}; holding flat",
        )

    def decide_exit(self, market: MarketState) -> ExitDecision:
        if not market.position_lines:
            return ExitDecision(ExitAction.HOLD, "flat; nothing to exit")
        net_vega = sum(line.position_vega for line in market.position_lines)
        if net_vega <= self.config.exit_vega_floor:
            return ExitDecision(
                ExitAction.FLATTEN,
                f"net vega {net_vega} at/below floor {self.config.exit_vega_floor}: "
                f"long-vol thesis gone (single-name vol collapsed)",
            )
        return ExitDecision(
            ExitAction.HOLD,
            f"net vega {net_vega} above floor {self.config.exit_vega_floor}; holding",
        )

    def construct(self, as_of: date, *, basket_id: str) -> Basket:
        members = self.data.top_n_members(as_of)
        if not members:
            raise DispersionConstructionError(
                as_of, f"no constituents resolved for index {self.config.index!r}"
            )

        straddle_legs = self._straddle_legs(members)
        forward_legs = self._forward_legs(straddle_legs, as_of)

        return Basket(
            basket_id=basket_id,
            trade_date=as_of,
            underlying=self.config.index,
            legs=straddle_legs + forward_legs,
            strategy_id=self.contract.strategy_id,
        )

    def rebalance(self, market: MarketState) -> RebalanceDecision:
        if not market.position_lines:
            return RebalanceDecision(0.0, "flat; no delta to hedge")
        net_delta = sum(line.position_delta for line in market.position_lines)
        band = DeltaHedgeBand(target=0.0, half_width=self.config.delta_band)
        instruction = decide_delta_hedge(net_delta, band)
        return RebalanceDecision(instruction.hedge_quantity, instruction.reason)

    def _straddle_legs(self, members: Sequence[BasketMember]) -> tuple[BasketLeg, ...]:
        q = self.config.contracts_per_name
        legs: list[BasketLeg] = []
        for member in members:
            legs.append(
                BasketLeg(
                    instrument_kind="option",
                    side="long",
                    quantity=q,
                    underlying=member.constituent,
                    tenor_label=self.config.straddle_tenor,
                    delta_band=_ATM_CALL_BAND,
                    surface_side=_SURFACE_CALL,
                )
            )
            legs.append(
                BasketLeg(
                    instrument_kind="option",
                    side="long",
                    quantity=q,
                    underlying=member.constituent,
                    tenor_label=self.config.straddle_tenor,
                    delta_band=_ATM_PUT_BAND,
                    surface_side=_SURFACE_PUT,
                )
            )
        return tuple(legs)

    def _forward_legs(
        self, straddle_legs: Sequence[BasketLeg], as_of: date
    ) -> tuple[BasketLeg, ...]:
        net_delta = self.data.net_dollar_delta(straddle_legs, as_of)
        if net_delta is None:
            raise DispersionConstructionError(
                as_of, "grid could not supply the straddle legs' net dollar delta to hedge"
            )
        unit_delta = self.data.forward_unit_dollar_delta(as_of)
        if unit_delta is None:
            raise DispersionConstructionError(
                as_of, "grid could not supply the synthetic forward's unit dollar delta"
            )
        if unit_delta == 0:
            raise DispersionConstructionError(
                as_of, "synthetic forward has zero unit dollar delta; cannot size a hedge"
            )

        forward_units = -net_delta / unit_delta
        if abs(forward_units) < self.config.min_hedge_units:
            return ()

        call_qty = -forward_units
        put_qty = forward_units
        return (
            BasketLeg(
                instrument_kind="option",
                side=_leg_side(call_qty),
                quantity=call_qty,
                underlying=self.config.index,
                tenor_label=self.config.straddle_tenor,
                delta_band=_ATM_CALL_BAND,
                surface_side=SURFACE_SIDE_COMBINED,
            ),
            BasketLeg(
                instrument_kind="option",
                side=_leg_side(put_qty),
                quantity=put_qty,
                underlying=self.config.index,
                tenor_label=self.config.straddle_tenor,
                delta_band=_ATM_PUT_BAND,
                surface_side=SURFACE_SIDE_COMBINED,
            ),
        )
