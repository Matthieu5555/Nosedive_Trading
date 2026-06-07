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

from datetime import date

from algotrading.infra.contracts import ProjectedOptionAnalytics, SurfaceParameters
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
    return JSONResponse(
        {
            "underlying": resolved_underlying,
            "trade_date": resolved_date.isoformat() if resolved_date else None,
            "n_maturities": len(maturities),
            "maturities": maturities,
        }
    )
