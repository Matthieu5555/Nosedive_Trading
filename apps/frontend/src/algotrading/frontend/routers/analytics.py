"""Projected-analytics router: the tenor × delta-band grid for one ticker/day (WS 1F).

Reads the persisted ``projected_option_analytics`` cells (and the matching fitted
``surface_parameters`` slices) back from the store for one underlying on one trade date and
serializes them **grouped by maturity** into the views the front renders:

* the fitted SVI slice (reusing ``surface_parameters_to_dict``) for the 3D surface trace,
* the smile points — implied vol vs delta across the 30Δ-put → ATM → 30Δ-call band, ordered by
  delta — for the 2D smile per maturity,
* the surface-grid cells (``log_moneyness``, ``implied_vol``, ``total_variance``) for the 3D
  trace, and
* the dollar Greeks, each tagged with the unit string stored on the cell (P0.2 / ADR 0036).

Field names conform to ADR 0029 (``forward_price``/``implied_vol``/``log_moneyness``/
``dollar_*``); the unit strings come from the stored cell, not invented here. The store opens
read-only. A malformed ``trade_date`` yields a labeled 400; an unknown ticker or empty grid
yields an empty ``maturities`` list with HTTP 200, never a 500.
"""

from __future__ import annotations

import math
from datetime import date

from algotrading.infra.contracts import (
    ProjectedOptionAnalytics,
    SurfaceGrid,
    SurfaceParameters,
)
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..context import AppContext
from ..serializers import (
    projected_option_analytics_to_dict,
    surface_parameters_to_dict,
)

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


def _context(request: Request) -> AppContext:
    return request.app.state.ctx


def _parse_date(value: str | None) -> date | None:
    if value is None:
        return None
    return date.fromisoformat(value)


def _maturity_key(maturity_years: float) -> str:
    """A stable string key for one maturity (rounded so float noise never splits a slice)."""
    return f"{maturity_years:.6f}"


def _group_by_maturity(
    cells: list[ProjectedOptionAnalytics],
    slices: list[SurfaceParameters],
) -> list[dict[str, object]]:
    """Group analytics cells (+ their fitted slice) into one entry per maturity.

    Within each maturity the cells become smile points ordered by delta (put → ATM → call), and
    each carries its dollar Greeks with unit strings. The fitted SVI slice for the same maturity
    is attached for the 3D surface trace.
    """
    slice_by_maturity = {_maturity_key(s.maturity_years): s for s in slices}
    grouped: dict[str, list[ProjectedOptionAnalytics]] = {}
    for cell in cells:
        grouped.setdefault(_maturity_key(cell.maturity_years), []).append(cell)

    entries: list[dict[str, object]] = []
    for key in sorted(grouped, key=float):
        maturity_cells = sorted(grouped[key], key=lambda c: c.target_delta)
        points = [projected_option_analytics_to_dict(cell) for cell in maturity_cells]
        # Tenor label is shared across a maturity's band points; take the first.
        tenor_label = maturity_cells[0].tenor_label
        fitted = slice_by_maturity.get(key)
        entries.append(
            {
                "maturity_years": maturity_cells[0].maturity_years,
                "tenor_label": tenor_label,
                "label": f"{tenor_label} ({maturity_cells[0].maturity_years:.3f}y)",
                "smile": {
                    "deltas": [cell.target_delta for cell in maturity_cells],
                    "implied_vols": [cell.implied_vol for cell in maturity_cells],
                    "log_moneyness": [cell.log_moneyness for cell in maturity_cells],
                },
                "surface_slice": (
                    surface_parameters_to_dict(fitted) if fitted is not None else None
                ),
                "points": points,
            }
        )
    return entries


def _maturities_from_surface_grid(
    grid: list[SurfaceGrid],
    slices: list[SurfaceParameters],
) -> list[dict[str, object]]:
    """Build the nappe-de-vol view from the persisted ``surface_grid`` + fitted slices.

    The fallback for the day before the tenor × delta-band projection has produced any cells:
    the surface fit (``iv_points`` → ``surface_parameters`` → ``surface_grid``) persists even
    when ``_build_projected_analytics`` skips an underlying for lack of a usable spot, so
    ``projected_option_analytics`` is empty while a full fitted surface is on disk. Each grid
    node carries ``total_variance`` on a (maturity_years, moneyness_bucket) cell, so the implied
    vol is ``sqrt(total_variance / maturity_years)``. The 3D ``VolSurface`` and ``SmileChart``
    read only ``smile.deltas``/``smile.implied_vols``, so the moneyness buckets stand in for the
    delta axis here (a coarser nappe than the delta-band grid, and labelled as moneyness); the
    per-cell dollar Greeks (``points``) are not available from the grid and come back empty until
    the projection lands. Once ``projected_option_analytics`` populates, the caller prefers it and
    this fallback is never reached — the upgrade to the rich grid is transparent to the front.
    """
    slice_by_maturity = {_maturity_key(s.maturity_years): s for s in slices}
    grouped: dict[str, list[SurfaceGrid]] = {}
    for cell in grid:
        grouped.setdefault(_maturity_key(cell.maturity_years), []).append(cell)

    entries: list[dict[str, object]] = []
    for key in sorted(grouped, key=float):
        cells = sorted(grouped[key], key=lambda c: c.moneyness_bucket)
        maturity_years = cells[0].maturity_years
        buckets = [cell.moneyness_bucket for cell in cells]
        implied_vols = [
            math.sqrt(cell.total_variance / cell.maturity_years)
            if cell.total_variance > 0.0 and cell.maturity_years > 0.0
            else 0.0
            for cell in cells
        ]
        fitted = slice_by_maturity.get(key)
        label = f"{maturity_years:.3f}y"
        entries.append(
            {
                "maturity_years": maturity_years,
                "tenor_label": label,
                "label": label,
                "smile": {
                    "deltas": buckets,
                    "implied_vols": implied_vols,
                    "log_moneyness": buckets,
                },
                "surface_slice": (
                    surface_parameters_to_dict(fitted) if fitted is not None else None
                ),
                "points": [],
            }
        )
    return entries


@router.get("")
def get_analytics(
    request: Request, underlying: str | None = None, trade_date: str | None = None
) -> JSONResponse:
    """Return the projected (tenor × delta-band) analytics grid for one ticker/day.

    ``underlying`` defaults to the context default; ``trade_date`` left ``None`` reads across
    every persisted day for the underlying. A malformed ``trade_date`` yields a labeled 400; an
    unknown ticker or empty grid yields an empty ``maturities`` list with HTTP 200.
    """
    ctx = _context(request)
    resolved_underlying = underlying or ctx.default_underlying
    try:
        resolved_date = _parse_date(trade_date)
    except ValueError:
        return JSONResponse(
            {"error": "bad_trade_date", "trade_date": trade_date}, status_code=400
        )
    # A version-blind read narrows to a single partition only when both trade_date and underlying
    # are given; with trade_date=None the store returns every partition, so filter by underlying
    # here in every case (an unknown ticker then resolves to an empty grid, not a 500).
    cells: list[ProjectedOptionAnalytics] = [
        row
        for row in ctx.store.read("projected_option_analytics", trade_date=resolved_date)
        if row.underlying == resolved_underlying
    ]
    slices: list[SurfaceParameters] = [
        row
        for row in ctx.store.read("surface_parameters", trade_date=resolved_date)
        if row.underlying == resolved_underlying
    ]
    maturities = _group_by_maturity(cells, slices)
    # The rich tenor × delta-band grid is the preferred view. When it is empty for the day (the
    # projection skipped this underlying for lack of a usable spot, while the surface fit still
    # persisted), fall back to the coarser nappe rebuilt from the fitted surface_grid so the front
    # shows a real vol surface rather than "No surface to plot yet". The upgrade to the rich grid,
    # once the projection lands, is transparent — this branch is simply no longer taken.
    source = "projected_option_analytics"
    if not maturities:
        grid = [
            row
            for row in ctx.store.read("surface_grid", trade_date=resolved_date)
            if row.underlying == resolved_underlying
        ]
        maturities = _maturities_from_surface_grid(grid, slices)
        if maturities:
            source = "surface_grid"
    return JSONResponse(
        {
            "underlying": resolved_underlying,
            "trade_date": resolved_date.isoformat() if resolved_date else None,
            "n_maturities": len(maturities),
            "source": source,
            "maturities": maturities,
        }
    )
