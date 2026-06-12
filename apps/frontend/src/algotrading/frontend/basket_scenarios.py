"""On-demand full-reprice stress surface for an operator-composed basket (Phase 2 / 2B).

Where :func:`algotrading.infra.risk.multileg.basket_risk` *sums* the stored per-leg dollar
Greeks (no reprice), this reconstructs one :class:`~algotrading.infra.risk.ContractValuationInput`
per option leg from the persisted :class:`~algotrading.infra.contracts.ProjectedOptionAnalytics`
grid and runs the trusted full-reprice surface (:func:`algotrading.infra.risk.stress_surface`)
over the composed basket — the cartesian (spot x vol) PnL surface the 2B page renders, computed
live for an operator-composed basket with no cron and no persisted ``scenario_results``.

This is the BFF's reconstruction path, deliberately distinct from the actor's
``valuation_join`` (which reads the *rich in-memory* actor results — the discount factor and QC
verdict the persisted contracts drop). Only the persisted grid is available here, so the
valuation is rebuilt under the projection's own documented conventions, stated once and asserted
by tests:

* ``spot = forward_price`` and ``carry = 0`` — the projection's carry-0 (spot==forward) view
  (``surfaces/projection.py`` ``SnapshotMarketState``), so spot and forward delta coincide.
* ``discount_factor`` — **backed out of the stored price**: ``df = row.price / p_free`` where
  ``p_free`` is the same contract priced rate-free (``df = 1``). The persisted row carries no
  discount factor, so this is the only way to recover the exact factor the projection used —
  and it makes the base reprice reproduce ``row.price`` by construction, robust to the
  projection's DF vintage (pre/post the F-SURF-01 1.1.0 fix). For a (near-)zero ``p_free``
  (deep OTM) the factor is undefined and falls back to 1.0; that cell's PnL is ~0 regardless.
* ``volatility = implied_vol``, ``strike``, ``maturity_years`` — copied from the row.
* ``option_right = "C"`` when the cell's signed ``target_delta >= 0`` else ``"P"``.
* ``exercise_style = "european"`` — correct for the SPX / SX5E index options the grid holds.
* ``multiplier`` / ``currency`` — from the underlying's ``instrument_master`` option row,
  supplied by the caller.

Stock legs are a linear overlay (``qty * spot * spot_shock``, vol-independent), added to the
option surface rather than forced through the option pricer. A leg that resolves to no grid cell
(ambiguous provider, missing spot, or missing instrument master) is a labelled
:class:`~algotrading.infra.risk.BasketGap`, never a silent zero.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date

from algotrading.core.config import ScenarioConfig
from algotrading.infra.contracts import Basket, ProjectedOptionAnalytics
from algotrading.infra.pricing import price
from algotrading.infra.risk import BasketGap, ContractValuationInput, PositionRisk, position_risk
from algotrading.infra.risk.multileg import CellKey, analytics_cell_key
from algotrading.infra.risk.stress_surface import stress_surface
from algotrading.infra.risk.valuation import pricing_state_for

# A synthetic portfolio id for the reprice lines — the basket is a hypothetical book, never a
# persisted portfolio (this never reaches the store).
_PORTFOLIO_ID = "basket-stress"
# Below this rate-free price the discount factor is not recoverable from the stored price
# (a deep-OTM near-zero value); fall back to an undiscounted factor — the cell's PnL is ~0.
_MIN_PRICE_FOR_DF = 1e-9


@dataclass(frozen=True, slots=True)
class BasketStressResult:
    """A composed basket's full-reprice PnL over the cartesian (spot x vol) stress grid.

    ``pnl_grid[i][j]`` is the basket's full-reprice PnL when spot is shocked by ``spot_axis[i]``
    (relative) and vol by ``vol_axis[j]`` (additive), summed over the resolved option legs plus
    the linear stock overlay. The worst cell is the largest loss over the grid. ``gaps`` names
    every leg that could not be repriced; ``n_resolved`` counts the legs that were.
    """

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
    """``"C"`` for a non-negative signed delta band, ``"P"`` otherwise (the pricer's codes)."""
    return "C" if target_delta >= 0.0 else "P"


def reconstruct_valuation(
    row: ProjectedOptionAnalytics, *, multiplier: float, currency: str
) -> ContractValuationInput:
    """Rebuild the valuation for one grid cell under the projection's conventions (module doc).

    The discount factor is backed out of the stored ``row.price`` so the base reprice reproduces
    it exactly, recovering the real factor the row carries no field for.
    """
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
    # Clamp to a valid factor: a stored price above its undiscounted value would imply df > 1
    # (no real discount curve does that) — pin to 1.0 rather than carry a nonsense factor.
    discount_factor = min(row.price / price_rate_free, 1.0)
    return dataclasses.replace(base, discount_factor=discount_factor)


def _index_rows(
    rows: Iterable[ProjectedOptionAnalytics],
) -> tuple[dict[CellKey, ProjectedOptionAnalytics], set[CellKey]]:
    """Index analytics rows by cell key, flagging any cell seeded by more than one provider.

    Mirrors :func:`multileg._index_rows_by_cell` (a cross-provider read can carry two rows for
    one ``(underlying, tenor_label, delta_band)``; resolving one silently would be a hidden
    non-deterministic pick, so the cell is recorded ambiguous and any leg on it becomes a gap).
    """
    by_cell: dict[CellKey, ProjectedOptionAnalytics] = {}
    ambiguous: set[CellKey] = set()
    for row in rows:
        key = analytics_cell_key(row.underlying, row.tenor_label, row.delta_band)
        if key in by_cell and by_cell[key].provider != row.provider:
            ambiguous.add(key)
        by_cell[key] = row
    return by_cell, ambiguous


def _worst_cell(
    spot_axis: tuple[float, ...],
    vol_axis: tuple[float, ...],
    pnl_grid: tuple[tuple[float, ...], ...],
) -> tuple[float, float, float]:
    """The (spot_shock, vol_shock, pnl) of the largest loss over the grid; ties by axis order."""
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
    """Full-reprice a composed basket over the cartesian (spot x vol) stress grid.

    Reconstructs an option line per resolved leg (module conventions), reprices them over the
    config-driven surface via :func:`stress_surface`, and adds the stock legs' linear overlay.
    Pure: no store, no clock. Every unresolved leg is a labelled :class:`BasketGap`; an empty or
    fully-unresolved basket still yields a valid (flat-zero + overlay) surface over the config
    axes, never a 500.
    """
    by_cell, ambiguous = _index_rows(analytics_rows)
    lines: list[PositionRisk] = []
    gaps: list[BasketGap] = []
    stock_notional: float = 0.0  # sum(qty * spot) over resolved stock legs (linear delta base)
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
        if key in ambiguous:
            gaps.append(
                BasketGap(leg.underlying, leg.tenor_label, leg.delta_band, "provider_ambiguous")
            )
            continue
        row = by_cell.get(key)
        if row is None:
            gaps.append(
                BasketGap(leg.underlying, leg.tenor_label, leg.delta_band, "no_analytics_row")
            )
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
    # Stock overlay: qty*spot*spot_shock, independent of vol — one constant per spot row, added
    # across every vol column.
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
