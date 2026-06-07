"""Constituent-list router: the index basket as it stood on a date (WS 1A).

Resolves ``members(index, as_of)`` — the no-look-ahead point-in-time gate — and returns the
historical basket ordered **price-first** (by the latest daily-bar close on or before
``as_of``; names without a bar sort last). The ``as_of`` is passed straight through to the
resolver; it is never defaulted to "today" and then applied to a past date (that would be
look-ahead). The store opens read-only. A malformed ``as_of`` yields a labeled 400; an unknown
index or a date before the index's first record yields an empty ``constituents`` list, never a
500.
"""

from __future__ import annotations

from datetime import date

from algotrading.infra.contracts import DailyBar, IndexConstituent
from algotrading.infra.universe import BasketMember, members
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..context import AppContext

router = APIRouter(prefix="/api/constituents", tags=["constituents"])


def _context(request: Request) -> AppContext:
    return request.app.state.ctx


def _parse_date(value: str | None) -> date | None:
    if value is None:
        return None
    return date.fromisoformat(value)


def _latest_close_by_underlying(bars: list[DailyBar], as_of: date) -> dict[str, float]:
    """The most recent daily-bar close at or before ``as_of`` per underlying.

    Bounding by ``as_of`` is the look-ahead gate on the *ordering* join: a future close must
    never leak into a past as-of view. Names with no bar in the window are absent from the map
    and sort last in the price-first order.
    """
    latest: dict[str, tuple[date, float]] = {}
    for bar in bars:
        if bar.trade_date > as_of:
            continue
        seen = latest.get(bar.underlying)
        if seen is None or bar.trade_date > seen[0]:
            latest[bar.underlying] = (bar.trade_date, bar.close)
    return {underlying: close for underlying, (_, close) in latest.items()}


def _interval_for(
    rows: list[IndexConstituent], constituent: str, as_of: date
) -> IndexConstituent | None:
    """The membership row whose half-open interval contains ``as_of`` for one name.

    This is the same interval the resolver selected; reading it back lets the payload carry the
    effective dates without changing the resolver's return shape. Among rows that contain the
    date, the most recently-known one wins (the latest restatement), matching the resolver.
    """
    candidates = [
        row
        for row in rows
        if row.constituent == constituent
        and row.effective_add_date <= as_of
        and (row.effective_remove_date is None or as_of < row.effective_remove_date)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda row: row.knowledge_date)


@router.get("")
def get_constituents(
    request: Request, index: str | None = None, as_of: str | None = None
) -> JSONResponse:
    """Return the point-in-time constituent basket for an index, price-first.

    ``index`` defaults to the context default underlying only as a label fallback; ``as_of``
    must be supplied for a historical view (it is passed verbatim to the resolver, never
    defaulted to today for a past date). When ``as_of`` is omitted the basket is resolved as of
    today's reconstruction date for a *current* view only.
    """
    ctx = _context(request)
    resolved_index = index or ctx.default_underlying
    try:
        as_of_date = _parse_date(as_of)
    except ValueError:
        return JSONResponse({"error": "bad_as_of", "as_of": as_of}, status_code=400)
    if as_of_date is None:
        as_of_date = date.today()

    basket: tuple[BasketMember, ...] = members(ctx.store, resolved_index, as_of_date)
    raw_rows: list[IndexConstituent] = [
        row for row in ctx.store.read("index_constituents") if row.index == resolved_index
    ]
    closes = _latest_close_by_underlying(ctx.store.read("daily_bar"), as_of_date)

    rows: list[dict[str, object]] = []
    for member in basket:
        interval = _interval_for(raw_rows, member.constituent, as_of_date)
        close = closes.get(member.constituent)
        rows.append(
            {
                "instrument_key": member.constituent,
                "symbol": member.constituent,
                "weight": member.weight,
                "effective_add_date": (
                    interval.effective_add_date.isoformat() if interval else None
                ),
                "effective_remove_date": (
                    interval.effective_remove_date.isoformat()
                    if interval and interval.effective_remove_date is not None
                    else None
                ),
                "latest_close": close,
            }
        )

    # Price-first: highest latest-close first; names without a bar (None) sort last; ties broken
    # by symbol so the order is deterministic.
    rows.sort(
        key=lambda r: (
            r["latest_close"] is None,
            -(r["latest_close"] if isinstance(r["latest_close"], int | float) else 0.0),
            r["symbol"],
        )
    )
    return JSONResponse(
        {
            "index": resolved_index,
            "as_of": as_of_date.isoformat(),
            "n_constituents": len(rows),
            "constituents": rows,
        }
    )
