"""Price-history router: read a ticker's daily OHLC bars back from the store (WS 1C/1E).

Reads the persisted ``daily_bar`` contract for one underlying over a ``[start, end]`` window
and serializes one row per day — ``trade_date``, ``open``/``high``/``low``/``close``/``volume``
plus provenance — for the candlestick chart. The store opens read-only (serving never writes;
the EOD cron is the sole writer, ADR 0034 §1). An unknown ticker or an empty/missing partition
returns an empty ``bars`` list with the labels, never a 500 (mirrors the surfaces router).
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..context import AppContext
from ..serializers import daily_bar_to_dict

router = APIRouter(prefix="/api/price-history", tags=["price-history"])


def _context(request: Request) -> AppContext:
    return request.app.state.ctx


def _parse_date(value: str | None) -> date | None:
    if value is None:
        return None
    return date.fromisoformat(value)


@router.get("")
def get_price_history(
    request: Request,
    underlying: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> JSONResponse:
    """Return daily OHLC bars for one underlying over an optional ``[start, end]`` window.

    ``start``/``end`` are inclusive ISO dates; either may be omitted for an open bound. A
    malformed date yields a labeled 400 (mirrors the surfaces router's ``bad_trade_date``); an
    unknown ticker or empty window yields an empty ``bars`` list with HTTP 200, never a 500.
    """
    ctx = _context(request)
    resolved_underlying = underlying or ctx.default_underlying
    try:
        start_date = _parse_date(start)
        end_date = _parse_date(end)
    except ValueError:
        return JSONResponse(
            {"error": "bad_date", "start": start, "end": end}, status_code=400
        )
    # A version-blind read narrowed to a single partition needs both trade_date and underlying;
    # with only an underlying the store returns every partition, so filter by underlying here.
    rows = [
        row
        for row in ctx.store.read("daily_bar")
        if row.underlying == resolved_underlying
        and (start_date is None or row.trade_date >= start_date)
        and (end_date is None or row.trade_date <= end_date)
    ]
    rows.sort(key=lambda row: row.trade_date)
    bars = [daily_bar_to_dict(row) for row in rows]
    return JSONResponse(
        {
            "underlying": resolved_underlying,
            "start": start_date.isoformat() if start_date else None,
            "end": end_date.isoformat() if end_date else None,
            "n_bars": len(bars),
            "bars": bars,
        }
    )
