"""Price and risk a multi-leg basket by **summation**, never a second pricing pass.

The whole point of 2A: a basket number is the *book-additive sum* of the per-position
dollar Greeks WS-1F already computed and stored on :class:`ProjectedOptionAnalytics`.
This module reads those rows and sums; it never calls a pricing engine and never imports
:class:`~algotrading.infra.risk.greeks.PositionRisk` — that legacy dollar-Greek home uses a
**different normalisation** (per-``$1`` / no-365) than the analytics grid (**per-1% / per-365**,
``pricing/dollar_greeks.py``), and mixing the two in one sum is silently wrong by 100×
(`tasks/PHASE2-prep-ready-on-commit.md`). So the one rule here is: the dollar Greeks summed
are exactly the ones on the analytics row, carried with the row's own unit strings.

This is NOT ``risk/basket.py`` — that is the index-variance identity (Eq 23,
``BasketVarianceResult``). A multi-leg position basket is a different thing; the two modules
must not be conflated.

An option leg resolves to one analytics cell by its grid coordinate
``(underlying, tenor_label, delta_band)`` (the grid is addressed by that, not by a canonical
instrument key). A stock leg resolves to its underlying's spot. A leg that resolves to nothing
is a **labelled gap** carrying the missing coordinate — never a silent zero, never a bare NaN —
and the basket aggregate reports the gap rather than absorbing it.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date

from algotrading.infra.contracts import (
    SURFACE_SIDE_COMBINED,
    Basket,
    BasketLeg,
    ProjectedOptionAnalytics,
)
from algotrading.infra.pricing import UNIT_STRINGS

# The five dollar-Greek names, in display order. delta/gamma/vega are always present on a
# row; theta/rho are additive-nullable (a row written before P0.2 carries None).
_DOLLAR_GREEKS = ("dollar_delta", "dollar_gamma", "dollar_vega", "dollar_theta", "dollar_rho")
# A stock share has no option Greeks: only a linear spot delta, the rest are real zeros.
_ALWAYS_PRESENT = ("dollar_delta", "dollar_gamma", "dollar_vega")

# A cell key is the analytics grid coordinate. tenor_label/delta_band are None for a stock leg.
CellKey = tuple[str, str | None, str | None]


def analytics_cell_key(
    underlying: str, tenor_label: str | None, delta_band: str | None
) -> CellKey:
    """The join key between a basket leg and a :class:`ProjectedOptionAnalytics` row.

    Built identically from a leg and from a row so the resolver matches them by value. It is
    an internal join key (a tuple), never a stored field and never a string pretending to be a
    canonical instrument key.
    """
    return (underlying, tenor_label, delta_band)


@dataclass(frozen=True, slots=True)
class BasketGap:
    """A leg that could not be priced, named by its coordinate and a machine-readable reason.

    Reasons: ``"no_analytics_row"`` (no matching grid cell), ``"provider_ambiguous"`` (the cell
    is seeded by more than one provider in the read scope — never silently pick one),
    ``"no_spot_for_stock_leg"`` (no spot for the underlying), ``"theta_unavailable"`` /
    ``"rho_unavailable"`` (the matched row's additive-nullable dollar-theta/rho is None).
    """

    underlying: str
    tenor_label: str | None
    delta_band: str | None
    reason: str


@dataclass(frozen=True, slots=True)
class LegRisk:
    """One leg's signed contribution to each basket dollar Greek, beside the leg itself.

    Preserved beside the aggregate because it is what proves the basket number is the sum of
    the per-leg analytics numbers (2C attributes off it). A contribution is ``None`` when the
    leg is unresolved, or when the underlying Greek is None on the matched row (theta/rho). Unit
    strings are carried through from the matched row, never re-derived.
    """

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
    # The matched analytics cell's context, carried through for the per-leg UI breakdown
    # (None for a stock leg or an unresolved leg). ADR-0029 names.
    forward_price: float | None
    implied_vol: float | None
    log_moneyness: float | None
    strike: float | None


@dataclass(frozen=True, slots=True)
class BasketRisk:
    """A basket priced and risked as the book-additive sum of its legs' analytics dollar Greeks.

    Each aggregate dollar Greek is ``math.fsum`` of its resolved legs' signed contributions —
    order-free, so shuffling the legs leaves it identical. An aggregate is ``None`` (with a
    matching :class:`BasketGap`) when any contributing leg lacks that Greek (theta/rho), so an
    incomplete total is labelled rather than silently understated. An empty basket is the
    labelled-empty state: every aggregate is the empty sum ``0.0`` with no legs and no gaps.
    """

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


def _index_rows_by_cell(
    rows: Iterable[ProjectedOptionAnalytics],
) -> tuple[dict[CellKey, ProjectedOptionAnalytics], set[CellKey]]:
    """Index analytics rows by cell key, flagging any cell seeded by more than one provider.

    The grid is provider-partitioned, so a cross-provider read can carry two rows for the same
    ``(underlying, tenor_label, delta_band)``. Those are genuinely distinct sources; picking one
    silently would be a hidden, non-deterministic choice. So an ambiguous cell is recorded and a
    leg that lands on it becomes a labelled gap rather than resolving to an arbitrary provider.
    """
    by_cell: dict[CellKey, ProjectedOptionAnalytics] = {}
    ambiguous: set[CellKey] = set()
    for row in rows:
        # A basket sums the combined surface — the forward-backing / attribution reference
        # (ADR 0048). The per-side put/call rows are an additive diagnostic, not part of the
        # book sum; skip them so the basket number is the combined book and the cross-provider
        # ambiguity check is not confused by a cell's three surface sides.
        if row.surface_side != SURFACE_SIDE_COMBINED:
            continue
        key = analytics_cell_key(row.underlying, row.tenor_label, row.delta_band)
        if key in by_cell and by_cell[key].provider != row.provider:
            ambiguous.add(key)
        by_cell[key] = row
    return by_cell, ambiguous


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
    """One option leg's signed contribution = ``leg.quantity * row.dollar_<greek>``.

    ``quantity`` is already signed by the side (validated at contract construction), so it is
    applied directly. theta/rho contributions are None when the row's are None (additive-nullable).
    """
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
    """One stock leg: a linear spot delta ``quantity * spot``; the option Greeks are real zeros."""
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
    """Price and risk a basket as the book-additive sum of its legs' analytics dollar Greeks.

    Pure: no store, no clock, no pricing engine, no config. ``analytics_rows`` are the WS-1F
    grid rows read back for the basket's ``(trade_date, underlying[, provider])``;
    ``spot_by_underlying`` supplies the spot each stock leg's linear delta needs. Returns a
    :class:`BasketRisk` whose aggregates are ``math.fsum`` over the resolved legs (order-free)
    and whose :class:`BasketGap` list names every leg that could not be fully priced.
    """
    by_cell, ambiguous = _index_rows_by_cell(analytics_rows)

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
        if key in ambiguous:
            leg_risks.append(_unresolved_leg(leg, "provider_ambiguous"))
            gaps.append(
                BasketGap(leg.underlying, leg.tenor_label, leg.delta_band, "provider_ambiguous")
            )
            continue
        row = by_cell.get(key)
        if row is None:
            leg_risks.append(_unresolved_leg(leg, "no_analytics_row"))
            gaps.append(
                BasketGap(leg.underlying, leg.tenor_label, leg.delta_band, "no_analytics_row")
            )
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
    """Sum the resolved legs' contributions per Greek (order-free) and pin one unit per Greek.

    delta/gamma/vega/price always aggregate over the resolved legs. theta/rho aggregate only when
    *every* resolved leg carries them; if any resolved leg's contribution is None, the aggregate
    for that Greek is None and a :class:`BasketGap` records why (an incomplete total is labelled,
    never silently understated to a partial sum).
    """
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
        # theta / rho: additive-nullable. A single missing contribution among resolved legs
        # makes the basket Greek unavailable, not a partial sum.
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
