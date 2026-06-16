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

    underlyings: Annotated[list[str], BeforeValidator(_dedupe_underlyings)] = []
    start: str | None = None
    end: str | None = None


_SINGLE_DEFAULT_WINDOW_DAYS = 730
_BATCH_DEFAULT_WINDOW_DAYS = 365
_GROUPED_READ_THRESHOLD = 4


def _history_payload(
    ctx: AppContext,
    underlyings: list[str],
    *,
    start_date: date | None,
    end_date: date,
) -> dict[str, object]:
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
        raise BadRequestError(
            {"error": "bad_date", "start": body.get("start"), "end": body.get("end")}
        ) from None
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
