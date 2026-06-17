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
from algotrading.infra.risk.greeks import net_lots
from algotrading.infra.risk.multileg import (
    analytics_cell_key,
    index_rows_by_cell_and_side,
    resolve_cell_side,
)
from algotrading.infra.risk.scenarios import (
    Scenario,
    scenario_grid,
    shock_valuation,
)
from algotrading.infra.risk.stress_surface import stress_surface
from algotrading.infra.risk.valuation import pricing_state_for

_PORTFOLIO_ID = "basket-stress"
_MIN_PRICE_FOR_DF = 1e-9


@dataclass(frozen=True, slots=True)
class BasketRateScenario:
    """One cell of the on-demand basket rate sweep.

    A single parallel rate move applied to the reconstructed option legs (the additive
    forward-fixed shock the engine already implements), and the book-summed full-reprice
    P&L delta it produces. Swept *beside* the spot × vol surface, not crossed with it
    (owner-ruled parallel sweep). Mirrors the persisted-path ``rate_scenarios_to_list`` cell.
    """

    scenario_id: str
    rate_shock: float
    scenario_pnl: float
    n_legs: int


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
    rate_sweep: tuple[BasketRateScenario, ...] = ()


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


def _rate_scenarios(config: ScenarioConfig) -> tuple[Scenario, ...]:
    return tuple(scenario for scenario in scenario_grid(config) if scenario.family == "rate")


def _rate_shock_pnl(line: PositionRisk, scenario: Scenario) -> float:
    """Full-reprice a single line under a parallel rate shock (additive, forward-fixed).

    Mirrors ``scenarios.full_reprice_pnl``/``shock_valuation`` (the same engine the persisted
    Risk path uses), but clamps the shocked discount factor to the pricer's valid (0, 1]
    domain. The basket reconstructs legs *rate-free* (parity-implied DF ≈ 1, so implied rate
    ≈ 0); a downward rate shock there would imply a sub-zero rate (DF > 1), which the pricer
    rejects. Flooring at DF = 1.0 floors the implied rate at 0 for that cell — an honest "no
    further discount benefit" rather than a crash or a fabricated number.
    """
    shocked = shock_valuation(line.valuation, scenario)
    if shocked.discount_factor > 1.0:
        shocked = dataclasses.replace(shocked, discount_factor=1.0)
    shocked_price = price(pricing_state_for(shocked)).price
    return (shocked_price - line.greeks.price) * line.scale


def _rate_sweep(
    lines: list[PositionRisk], config: ScenarioConfig
) -> tuple[BasketRateScenario, ...]:
    """Sweep the configured rate scenarios over the reconstructed option legs.

    A *new* on-demand reprice in the basket engine (the whole point of this remainder): the
    engine sweeps, the router only serializes. Empty when ``config.rate_shocks`` is unset,
    keeping the payload byte-identical to the spot×vol-only contract. Stock legs carry no rate
    sensitivity and are excluded (only ``lines`` — the reconstructed option positions —
    contribute). Returns one book-summed cell per configured shock, sorted ascending.
    """
    rate_scenarios = _rate_scenarios(config)
    if not rate_scenarios:
        return ()
    netted = net_lots(lines)
    if not netted:
        return ()
    n_legs = len(netted)
    sweep = [
        BasketRateScenario(
            scenario_id=scenario.scenario_id,
            rate_shock=scenario.rate_shock,
            scenario_pnl=math.fsum(_rate_shock_pnl(line, scenario) for line in netted),
            n_legs=n_legs,
        )
        for scenario in rate_scenarios
    ]
    sweep.sort(key=lambda cell: cell.rate_shock)
    return tuple(sweep)


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
    rate_sweep = _rate_sweep(lines, config)

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
        rate_sweep=rate_sweep,
    )
