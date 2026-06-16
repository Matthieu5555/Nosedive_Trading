from __future__ import annotations

import dataclasses
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date

from algotrading.core.config import ScenarioConfig
from algotrading.infra.contracts import (
    Basket,
    ProjectedOptionAnalytics,
)
from algotrading.infra.pricing import price
from algotrading.infra.risk import BasketGap, ContractValuationInput, PositionRisk, position_risk
from algotrading.infra.risk.multileg import (
    analytics_cell_key,
    index_rows_by_cell_and_side,
    resolve_cell_side,
)
from algotrading.infra.risk.stress_surface import stress_surface
from algotrading.infra.risk.valuation import pricing_state_for

_PORTFOLIO_ID = "basket-stress"
_MIN_PRICE_FOR_DF = 1e-9


@dataclass(frozen=True, slots=True)
class BasketStressResult:

    basket_id: str
    trade_date: date
    underlying: str
    spot_axis: tuple[float, ...]
    vol_axis: tuple[float, ...]
    pnl_grid: tuple[tuple[float, ...], ...]
    scenario_version: str
    worst_spot_shock: float
    worst_vol_shock: float
    worst_pnl: float
    n_legs: int
    n_resolved: int
    gaps: tuple[BasketGap, ...]


def _option_right(target_delta: float) -> str:
    return "C" if target_delta >= 0.0 else "P"


def reconstruct_valuation(
    row: ProjectedOptionAnalytics, *, multiplier: float, currency: str
) -> ContractValuationInput:
    base = ContractValuationInput(
        contract_key=f"{row.underlying}|{row.tenor_label}|{row.delta_band}",
        underlying=row.underlying,
        option_right=_option_right(row.target_delta),
        exercise_style="european",
        strike=row.strike,
        maturity_years=row.maturity_years,
        spot=row.forward_price,
        carry=0.0,
        volatility=row.implied_vol,
        discount_factor=1.0,
        multiplier=multiplier,
        currency=currency,
    )
    price_rate_free = price(pricing_state_for(base)).price
    if price_rate_free <= _MIN_PRICE_FOR_DF:
        return base
    discount_factor = min(row.price / price_rate_free, 1.0)
    return dataclasses.replace(base, discount_factor=discount_factor)


def _worst_cell(
    spot_axis: tuple[float, ...],
    vol_axis: tuple[float, ...],
    pnl_grid: tuple[tuple[float, ...], ...],
) -> tuple[float, float, float]:
    worst = (spot_axis[0], vol_axis[0], pnl_grid[0][0])
    for i, spot_shock in enumerate(spot_axis):
        for j, vol_shock in enumerate(vol_axis):
            if pnl_grid[i][j] < worst[2]:
                worst = (spot_shock, vol_shock, pnl_grid[i][j])
    return worst


def basket_stress(
    basket: Basket,
    *,
    analytics_rows: Iterable[ProjectedOptionAnalytics],
    multiplier: float | None,
    currency: str | None,
    spot_by_underlying: Mapping[str, float],
    config: ScenarioConfig,
) -> BasketStressResult:
    by_cell_side, ambiguous = index_rows_by_cell_and_side(analytics_rows)
    lines: list[PositionRisk] = []
    gaps: list[BasketGap] = []
    stock_notional: float = 0.0
    n_resolved = 0

    for leg in basket.legs:
        if leg.instrument_kind == "stock":
            spot = spot_by_underlying.get(leg.underlying)
            if spot is None:
                gaps.append(BasketGap(leg.underlying, None, None, "no_spot_for_stock_leg"))
            else:
                stock_notional = math.fsum([stock_notional, leg.quantity * spot])
                n_resolved += 1
            continue

        key = analytics_cell_key(leg.underlying, leg.tenor_label, leg.delta_band)
        row, reason = resolve_cell_side(
            by_cell_side, ambiguous, key=key, surface_side=leg.surface_side
        )
        if row is None:
            assert reason is not None
            gaps.append(BasketGap(leg.underlying, leg.tenor_label, leg.delta_band, reason))
            continue
        if multiplier is None or currency is None:
            gaps.append(
                BasketGap(leg.underlying, leg.tenor_label, leg.delta_band, "no_instrument_master")
            )
            continue
        valuation = reconstruct_valuation(row, multiplier=multiplier, currency=currency)
        lines.append(
            position_risk(portfolio_id=_PORTFOLIO_ID, quantity=leg.quantity, valuation=valuation)
        )
        n_resolved += 1

    surface = stress_surface(lines, config)
    spot_axis, vol_axis = surface.spot_axis, surface.vol_axis
    pnl_grid = tuple(
        tuple(
            surface.pnl_grid[i][j] + stock_notional * spot_axis[i]
            for j in range(len(vol_axis))
        )
        for i in range(len(spot_axis))
    )
    worst_spot, worst_vol, worst_pnl = _worst_cell(spot_axis, vol_axis, pnl_grid)

    return BasketStressResult(
        basket_id=basket.basket_id,
        trade_date=basket.trade_date,
        underlying=basket.underlying,
        spot_axis=spot_axis,
        vol_axis=vol_axis,
        pnl_grid=pnl_grid,
        scenario_version=surface.scenario_version,
        worst_spot_shock=worst_spot,
        worst_vol_shock=worst_vol,
        worst_pnl=worst_pnl,
        n_legs=len(basket.legs),
        n_resolved=n_resolved,
        gaps=tuple(gaps),
    )
