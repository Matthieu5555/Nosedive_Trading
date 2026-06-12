"""Price-history router: read a ticker's daily OHLC bars back from the store (WS 1C/1E).

Reads the persisted ``daily_bar`` contract for one underlying over a ``[start, end]`` window
and serializes one row per day — ``trade_date``, ``open``/``high``/``low``/``close``/``volume``
plus provenance — for the candlestick chart. The store opens read-only (serving never writes;
the EOD cron is the sole writer, ADR 0034 §1). An unknown ticker or an empty/missing partition
returns an empty ``bars`` list with the labels, never a 500 (mirrors the surfaces router).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, BeforeValidator, ValidationError
from starlette.concurrency import run_in_threadpool

from ..context import AppContext
from ..deps import BadRequestError, CtxDep, DateWindowDep, parse_json_body
from ..serializers import daily_bar_to_dict

router = APIRouter(prefix="/api/price-history", tags=["price-history"])


def _dedupe_underlyings(raw: object) -> list[str]:
    """Return requested underlyings in first-seen order, accepting JSON or query shapes."""
    values: list[str] = []
    if isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, list):
        values = [item for item in raw if isinstance(item, str)]
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        for part in value.split(","):
            symbol = part.strip()
            if symbol and symbol not in seen:
                seen.add(symbol)
                result.append(symbol)
    return result


class BatchHistoryIn(BaseModel):
    """``POST /batch`` body: the symbols plus an optional inclusive ISO-date window.

    ``underlyings`` accepts the same lenient shapes the query variant does (a list, a
    comma-separated string, non-strings silently dropped) via the shared dedupe; the
    dates stay raw strings here and are parsed by the handler so a malformed value keeps
    the historical ``bad_date`` 400 (not FastAPI's 422).
    """

    underlyings: Annotated[list[str], BeforeValidator(_dedupe_underlyings)] = []
    start: str | None = None
    end: str | None = None


# Default lookback for the single-ticker endpoint: 2 years keeps rendering and query
# times low while covering the chart's initial view.
_SINGLE_DEFAULT_WINDOW_DAYS = 730
# A batch left unbounded ("all available history") walked the whole per-name partition
# tree once per ticker — measured 5–8s/name on the live store, i.e. minutes for one
# basket. The batch therefore defaults to this window when ``start`` is omitted; an
# explicit ``start`` still wins. One year covers the chart preload the front does.
_BATCH_DEFAULT_WINDOW_DAYS = 365
# Above this many names, one grouped range-read (all names in one store scan, filtered
# in memory) beats per-name reads — measured 46s for the whole table over 2y vs 5–8s
# per single name.
_GROUPED_READ_THRESHOLD = 4


def _history_payload(
    ctx: AppContext,
    underlyings: list[str],
    *,
    start_date: date | None,
    end_date: date,
) -> dict[str, object]:
    """Read all requested histories and return one grouped payload.

    ``start_date=None`` means the default :data:`_BATCH_DEFAULT_WINDOW_DAYS` window up to
    ``end_date`` — never "all available history", which on the live store costs minutes
    per basket (the single-ticker endpoint likewise keeps its bounded default for cheap
    chart loads). Large baskets are served from ONE grouped range-read of the table
    rather than a per-name partition walk.
    """
    if start_date is None:
        start_date = end_date - timedelta(days=_BATCH_DEFAULT_WINDOW_DAYS)
    requested = set(underlyings)
    by_underlying: dict[str, list[Any]] = {symbol: [] for symbol in underlyings}
    if len(underlyings) > _GROUPED_READ_THRESHOLD:
        for row in ctx.store.read("daily_bar", start_date=start_date, end_date=end_date):
            if row.underlying in requested:
                by_underlying[row.underlying].append(row)
    else:
        for underlying in underlyings:
            by_underlying[underlying] = ctx.store.read(
                "daily_bar",
                underlying=underlying,
                start_date=start_date,
                end_date=end_date,
            )
    histories: list[dict[str, object]] = []
    total_bars = 0
    for underlying in underlyings:
        rows = by_underlying[underlying]
        rows.sort(key=lambda row: row.trade_date)
        bars = [daily_bar_to_dict(row) for row in rows]
        total_bars += len(bars)
        histories.append(
            {
                "underlying": underlying,
                "start": start_date.isoformat() if start_date is not None else None,
                "end": end_date.isoformat(),
                "n_bars": len(bars),
                "bars": bars,
            }
        )
    return {
        "underlyings": underlyings,
        "start": start_date.isoformat() if start_date is not None else None,
        "end": end_date.isoformat(),
        "n_underlyings": len(underlyings),
        "n_loaded": sum(1 for item in histories if item["n_bars"] != 0),
        "n_empty": sum(1 for item in histories if item["n_bars"] == 0),
        "n_bars": total_bars,
        "histories": histories,
    }


@router.get("/batch")
def get_price_history_batch(
    ctx: CtxDep,
    window: DateWindowDep,
    underlyings: Annotated[list[str] | None, Query()] = None,
) -> JSONResponse:
    """Return grouped OHLC histories for the requested underlyings.

    Query callers can pass repeated ``underlyings=AAA&underlyings=BBB`` or a comma-separated
    value. ``start`` omitted means the bounded default window up to ``end``.
    """
    return JSONResponse(
        _history_payload(
            ctx,
            _dedupe_underlyings(underlyings),
            start_date=window.start,
            end_date=window.end or date.today(),
        )
    )


@router.post("/batch")
async def post_price_history_batch(ctx: CtxDep, request: Request) -> JSONResponse:
    """POST variant for large baskets whose symbols should not be encoded into a long URL."""
    body = await parse_json_body(request, error="bad_batch")
    if not isinstance(body, dict):
        raise BadRequestError(
            {"error": "bad_batch", "detail": "body must be a JSON object"}
        )
    try:
        parsed = BatchHistoryIn.model_validate(body)
        start_date = date.fromisoformat(parsed.start) if parsed.start is not None else None
        end_date = date.fromisoformat(parsed.end) if parsed.end is not None else date.today()
    except (ValidationError, ValueError):
        # The only model fields that can fail are the dates (the symbol dedupe is total),
        # so this is the same labelled 400 the date parse always produced.
        raise BadRequestError(
            {"error": "bad_date", "start": body.get("start"), "end": body.get("end")}
        ) from None
    # The store read is blocking I/O over many Parquet files; run it in the threadpool so
    # this async handler never parks the event loop (a loop-blocking batch starved every
    # other endpoint — /healthz included — for minutes, observed live).
    payload = await run_in_threadpool(
        _history_payload,
        ctx,
        parsed.underlyings,
        start_date=start_date,
        end_date=end_date,
    )
    return JSONResponse(payload)


@router.get("")
def get_price_history(
    ctx: CtxDep, window: DateWindowDep, underlying: str | None = None
) -> JSONResponse:
    """Return daily OHLC bars for one underlying over an optional ``[start, end]`` window.

    ``start``/``end`` are inclusive ISO dates; either may be omitted for an open bound. A
    malformed date yields a labeled 400 (mirrors the surfaces router's ``bad_trade_date``); an
    unknown ticker or empty window yields an empty ``bars`` list with HTTP 200, never a 500.
    """
    resolved_underlying = underlying or ctx.default_underlying
    resolved_end = window.end or date.today()
    resolved_start = window.start or (
        resolved_end - timedelta(days=_SINGLE_DEFAULT_WINDOW_DAYS)
    )

    rows = ctx.store.read(
        "daily_bar",
        underlying=resolved_underlying,
        start_date=resolved_start,
        end_date=resolved_end,
    )
    rows.sort(key=lambda row: row.trade_date)
    bars = [daily_bar_to_dict(row) for row in rows]
    return JSONResponse(
        {
            "underlying": resolved_underlying,
            "start": resolved_start.isoformat(),
            "end": resolved_end.isoformat(),
            "n_bars": len(bars),
            "bars": bars,
        }
    )
