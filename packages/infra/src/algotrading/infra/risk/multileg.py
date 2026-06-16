from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date

from algotrading.infra.contracts import (
    Basket,
    BasketLeg,
    ProjectedOptionAnalytics,
)
from algotrading.infra.pricing import UNIT_STRINGS

_DOLLAR_GREEKS = ("dollar_delta", "dollar_gamma", "dollar_vega", "dollar_theta", "dollar_rho")
_ALWAYS_PRESENT = ("dollar_delta", "dollar_gamma", "dollar_vega")

CellKey = tuple[str, str | None, str | None]

SideCellIndex = dict[CellKey, dict[str, ProjectedOptionAnalytics]]


def analytics_cell_key(
    underlying: str, tenor_label: str | None, delta_band: str | None
) -> CellKey:
    return (underlying, tenor_label, delta_band)


@dataclass(frozen=True, slots=True)
class BasketGap:

    underlying: str
    tenor_label: str | None
    delta_band: str | None
    reason: str


@dataclass(frozen=True, slots=True)
class LegRisk:

    leg: BasketLeg
    resolved: bool
    gap_reason: str | None
    dollar_delta: float | None
    dollar_gamma: float | None
    dollar_vega: float | None
    dollar_theta: float | None
    dollar_rho: float | None
    price: float | None
    dollar_delta_unit: str | None
    dollar_gamma_unit: str | None
    dollar_vega_unit: str | None
    dollar_theta_unit: str | None
    dollar_rho_unit: str | None
    forward_price: float | None
    implied_vol: float | None
    log_moneyness: float | None
    strike: float | None


@dataclass(frozen=True, slots=True)
class BasketRisk:

    basket_id: str
    trade_date: date
    underlying: str
    dollar_delta: float | None
    dollar_gamma: float | None
    dollar_vega: float | None
    dollar_theta: float | None
    dollar_rho: float | None
    price: float | None
    dollar_delta_unit: str | None
    dollar_gamma_unit: str | None
    dollar_vega_unit: str | None
    dollar_theta_unit: str | None
    dollar_rho_unit: str | None
    legs: tuple[LegRisk, ...]
    gaps: tuple[BasketGap, ...]


def index_rows_by_cell_and_side(
    rows: Iterable[ProjectedOptionAnalytics],
) -> tuple[SideCellIndex, set[tuple[CellKey, str]]]:
    by_cell_side: SideCellIndex = {}
    ambiguous: set[tuple[CellKey, str]] = set()
    for row in rows:
        key = analytics_cell_key(row.underlying, row.tenor_label, row.delta_band)
        side_rows = by_cell_side.setdefault(key, {})
        existing = side_rows.get(row.surface_side)
        if existing is not None and existing.provider != row.provider:
            ambiguous.add((key, row.surface_side))
        side_rows[row.surface_side] = row
    return by_cell_side, ambiguous


def resolve_cell_side(
    by_cell_side: SideCellIndex,
    ambiguous: set[tuple[CellKey, str]],
    *,
    key: CellKey,
    surface_side: str,
) -> tuple[ProjectedOptionAnalytics | None, str | None]:
    if (key, surface_side) in ambiguous:
        return None, "provider_ambiguous"
    side_rows = by_cell_side.get(key)
    if not side_rows:
        return None, "no_analytics_row"
    row = side_rows.get(surface_side)
    if row is None:
        return None, "surface_side_unavailable"
    return row, None


def _unresolved_leg(leg: BasketLeg, reason: str) -> LegRisk:
    return LegRisk(
        leg=leg, resolved=False, gap_reason=reason,
        dollar_delta=None, dollar_gamma=None, dollar_vega=None,
        dollar_theta=None, dollar_rho=None, price=None,
        dollar_delta_unit=None, dollar_gamma_unit=None, dollar_vega_unit=None,
        dollar_theta_unit=None, dollar_rho_unit=None,
        forward_price=None, implied_vol=None, log_moneyness=None, strike=None,
    )


def _option_leg_risk(leg: BasketLeg, row: ProjectedOptionAnalytics) -> LegRisk:
    q = leg.quantity
    theta = None if row.dollar_theta is None else q * row.dollar_theta
    rho = None if row.dollar_rho is None else q * row.dollar_rho
    return LegRisk(
        leg=leg, resolved=True, gap_reason=None,
        dollar_delta=q * row.dollar_delta,
        dollar_gamma=q * row.dollar_gamma,
        dollar_vega=q * row.dollar_vega,
        dollar_theta=theta,
        dollar_rho=rho,
        price=q * row.price,
        dollar_delta_unit=row.dollar_delta_unit,
        dollar_gamma_unit=row.dollar_gamma_unit,
        dollar_vega_unit=row.dollar_vega_unit,
        dollar_theta_unit=row.dollar_theta_unit,
        dollar_rho_unit=row.dollar_rho_unit,
        forward_price=row.forward_price,
        implied_vol=row.implied_vol,
        log_moneyness=row.log_moneyness,
        strike=row.strike,
    )


def _stock_leg_risk(leg: BasketLeg, spot: float) -> LegRisk:
    return LegRisk(
        leg=leg, resolved=True, gap_reason=None,
        dollar_delta=leg.quantity * spot,
        dollar_gamma=0.0, dollar_vega=0.0, dollar_theta=0.0, dollar_rho=0.0,
        price=leg.quantity * spot,
        dollar_delta_unit=UNIT_STRINGS["dollar_delta"],
        dollar_gamma_unit=None, dollar_vega_unit=None,
        dollar_theta_unit=None, dollar_rho_unit=None,
        forward_price=None, implied_vol=None, log_moneyness=None, strike=None,
    )


def basket_risk(
    basket: Basket,
    *,
    analytics_rows: Iterable[ProjectedOptionAnalytics],
    spot_by_underlying: Mapping[str, float],
) -> BasketRisk:
    by_cell_side, ambiguous = index_rows_by_cell_and_side(analytics_rows)

    leg_risks: list[LegRisk] = []
    gaps: list[BasketGap] = []
    for leg in basket.legs:
        if leg.instrument_kind == "stock":
            spot = spot_by_underlying.get(leg.underlying)
            if spot is None:
                leg_risks.append(_unresolved_leg(leg, "no_spot_for_stock_leg"))
                gaps.append(BasketGap(leg.underlying, None, None, "no_spot_for_stock_leg"))
            else:
                leg_risks.append(_stock_leg_risk(leg, spot))
            continue

        key = analytics_cell_key(leg.underlying, leg.tenor_label, leg.delta_band)
        row, reason = resolve_cell_side(
            by_cell_side, ambiguous, key=key, surface_side=leg.surface_side
        )
        if row is None:
            assert reason is not None
            leg_risks.append(_unresolved_leg(leg, reason))
            gaps.append(BasketGap(leg.underlying, leg.tenor_label, leg.delta_band, reason))
            continue
        leg_risks.append(_option_leg_risk(leg, row))

    aggregate, units, agg_gaps = _aggregate(leg_risks)
    gaps.extend(agg_gaps)

    return BasketRisk(
        basket_id=basket.basket_id,
        trade_date=basket.trade_date,
        underlying=basket.underlying,
        dollar_delta=aggregate["dollar_delta"],
        dollar_gamma=aggregate["dollar_gamma"],
        dollar_vega=aggregate["dollar_vega"],
        dollar_theta=aggregate["dollar_theta"],
        dollar_rho=aggregate["dollar_rho"],
        price=aggregate["price"],
        dollar_delta_unit=units["dollar_delta"],
        dollar_gamma_unit=units["dollar_gamma"],
        dollar_vega_unit=units["dollar_vega"],
        dollar_theta_unit=units["dollar_theta"],
        dollar_rho_unit=units["dollar_rho"],
        legs=tuple(leg_risks),
        gaps=tuple(gaps),
    )


def _aggregate(
    leg_risks: list[LegRisk],
) -> tuple[dict[str, float | None], dict[str, str | None], list[BasketGap]]:
    resolved = [lr for lr in leg_risks if lr.resolved]
    aggregate: dict[str, float | None] = {}
    units: dict[str, str | None] = {}
    gaps: list[BasketGap] = []

    for greek in _DOLLAR_GREEKS:
        contributions = [getattr(lr, greek) for lr in resolved]
        unit_attr = f"{greek}_unit"
        units[greek] = next(
            (getattr(lr, unit_attr) for lr in resolved if getattr(lr, unit_attr) is not None),
            None,
        )
        if greek in _ALWAYS_PRESENT:
            aggregate[greek] = math.fsum(c for c in contributions if c is not None)
            continue
        if any(c is None for c in contributions):
            aggregate[greek] = None
            for lr in resolved:
                if getattr(lr, greek) is None:
                    gaps.append(
                        BasketGap(
                            lr.leg.underlying, lr.leg.tenor_label, lr.leg.delta_band,
                            f"{greek.removeprefix('dollar_')}_unavailable",
                        )
                    )
        else:
            aggregate[greek] = math.fsum(c for c in contributions if c is not None)

    aggregate["price"] = math.fsum(lr.price for lr in resolved if lr.price is not None)
    return aggregate, units, gaps
