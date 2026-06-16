from __future__ import annotations

import dataclasses
import math
from collections.abc import Iterable
from dataclasses import dataclass

from algotrading.infra.pricing import PriceGreeks, price

from .bumps import DEFAULT_BUMPS, BumpSpec
from .valuation import ContractValuationInput, ValuationError, pricing_state_for


@dataclass(frozen=True, slots=True)
class PositionRisk:

    portfolio_id: str
    quantity: float
    valuation: ContractValuationInput
    greeks: PriceGreeks

    @property
    def contract_key(self) -> str:
        return self.valuation.contract_key

    @property
    def underlying(self) -> str:
        return self.valuation.underlying

    @property
    def scale(self) -> float:
        return self.valuation.multiplier * self.quantity

    @property
    def market_value(self) -> float:
        return self.greeks.price * self.scale

    @property
    def position_delta(self) -> float:
        return self.greeks.delta * self.scale

    @property
    def position_gamma(self) -> float:
        return self.greeks.gamma * self.scale

    @property
    def position_vega(self) -> float:
        return self.greeks.vega * self.scale

    @property
    def position_theta(self) -> float:
        return self.greeks.theta * self.scale


def position_risk(
    *,
    portfolio_id: str,
    quantity: float,
    valuation: ContractValuationInput,
    steps: int | None = None,
) -> PositionRisk:
    if not math.isfinite(quantity):
        raise ValuationError("quantity", quantity, "must be a finite number")
    state = pricing_state_for(valuation)
    greeks = price(state, steps=steps) if steps is not None else price(state)
    return PositionRisk(
        portfolio_id=portfolio_id, quantity=quantity, valuation=valuation, greeks=greeks
    )


class LotConsistencyError(Exception):

    def __init__(self, portfolio_id: str, contract_key: str) -> None:
        self.portfolio_id = portfolio_id
        self.contract_key = contract_key
        super().__init__(
            f"lots of {contract_key!r} in portfolio {portfolio_id!r} disagree on market state"
        )


def net_lots(lines: Iterable[PositionRisk]) -> list[PositionRisk]:
    grouped: dict[tuple[str, str], list[PositionRisk]] = {}
    for line in lines:
        grouped.setdefault((line.portfolio_id, line.contract_key), []).append(line)
    netted: list[PositionRisk] = []
    for (portfolio_id, contract_key), lots in grouped.items():
        canonical = lots[0]
        if any(lot.valuation != canonical.valuation for lot in lots[1:]):
            raise LotConsistencyError(portfolio_id, contract_key)
        if len(lots) == 1:
            netted.append(canonical)
            continue
        total_quantity = math.fsum(lot.quantity for lot in lots)
        netted.append(dataclasses.replace(canonical, quantity=total_quantity))
    netted.sort(key=lambda line: line.contract_key)
    return netted


def _price_of(valuation: ContractValuationInput) -> float:
    return price(pricing_state_for(valuation)).price


def central_difference_greeks(
    valuation: ContractValuationInput, *, bumps: BumpSpec = DEFAULT_BUMPS
) -> PriceGreeks:
    spot = valuation.spot
    h_first = bumps.spot_first(spot)
    h_second = bumps.spot_second(spot)

    def with_spot(new_spot: float) -> ContractValuationInput:
        return dataclasses.replace(valuation, spot=new_spot)

    base_price = _price_of(valuation)
    delta = (_price_of(with_spot(spot + h_first)) - _price_of(with_spot(spot - h_first))) / (
        2.0 * h_first
    )
    gamma = (
        _price_of(with_spot(spot + h_second))
        - 2.0 * base_price
        + _price_of(with_spot(spot - h_second))
    ) / (h_second * h_second)

    h_vol = bumps.vol_abs
    vega = (
        _price_of(dataclasses.replace(valuation, volatility=valuation.volatility + h_vol))
        - _price_of(dataclasses.replace(valuation, volatility=valuation.volatility - h_vol))
    ) / (2.0 * h_vol)

    rate = valuation.implied_rate
    h_t = bumps.time_abs

    def with_maturity(new_t: float) -> ContractValuationInput:
        return dataclasses.replace(
            valuation, maturity_years=new_t, discount_factor=math.exp(-rate * new_t)
        )

    theta = -(
        _price_of(with_maturity(valuation.maturity_years + h_t))
        - _price_of(with_maturity(valuation.maturity_years - h_t))
    ) / (2.0 * h_t)

    return PriceGreeks(
        price=base_price,
        delta=delta,
        gamma=gamma,
        vega=vega,
        theta=theta,
        rho=-valuation.maturity_years * base_price,
        vanna=0.0,
        volga=0.0,
        charm=0.0,
    )
