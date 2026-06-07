"""Surfaces router: read fitted SVI surfaces back from the store.

Reads the persisted ``surface_parameters`` contract for one underlying (optionally one
trade date) and serializes the SVI parameters + fit diagnostics + provenance. The web
Surfaces page renders the smile from the SVI parameters. A missing partition returns an
empty ``slices`` list, never a 500.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..context import AppContext
from ..serializers import surface_parameters_to_dict

router = APIRouter(prefix="/api/surfaces", tags=["surfaces"])


def _context(request: Request) -> AppContext:
    return request.app.state.ctx


def _parse_trade_date(value: str | None) -> date | None:
    if value is None:
        return None
    return date.fromisoformat(value)


@router.get("/underlyings")
def list_surface_underlyings(request: Request) -> JSONResponse:
    """List the underlyings that have a persisted surface."""
    ctx = _context(request)
    partitions = ctx.store.list_partitions("surface_parameters")
    underlyings = sorted({underlying for _, underlying in partitions})
    return JSONResponse({"underlyings": underlyings})


@router.get("")
def get_surface(
    request: Request, underlying: str | None = None, trade_date: str | None = None
) -> JSONResponse:
    """Return the fitted SVI slices for an underlying (defaults to the context default)."""
    ctx = _context(request)
    resolved_underlying = underlying or ctx.default_underlying
    try:
        resolved_date = _parse_trade_date(trade_date)
    except ValueError:
        return JSONResponse(
            {"error": "bad_trade_date", "trade_date": trade_date}, status_code=400
        )
    # A version-blind read narrows to one partition only when both trade_date and underlying are
    # given; with trade_date=None the store returns every underlying's slices, so filter by
    # underlying here so a per-underlying query never bleeds in another name's surface.
    rows = [
        row
        for row in ctx.store.read("surface_parameters", trade_date=resolved_date)
        if row.underlying == resolved_underlying
    ]
    rows.sort(key=lambda row: row.maturity_years)
    slices = [surface_parameters_to_dict(row) for row in rows]
    return JSONResponse(
        {
            "underlying": resolved_underlying,
            "trade_date": resolved_date.isoformat() if resolved_date else None,
            "n_slices": len(slices),
            "slices": slices,
        }
    )
