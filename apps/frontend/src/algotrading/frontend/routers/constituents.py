from __future__ import annotations

from datetime import date, timedelta

from algotrading.infra.contracts import DailyBar, IndexConstituent
from algotrading.infra.universe import BasketMember, members
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..deps import AsOfDep, CtxDep

router = APIRouter(prefix="/api/constituents", tags=["constituents"])


def _latest_close_by_underlying(bars: list[DailyBar], as_of: date) -> dict[str, float]:
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
    ctx: CtxDep, as_of: AsOfDep, index: str | None = None
) -> JSONResponse:
    resolved_index = index or ctx.default_underlying
    as_of_date = as_of if as_of is not None else date.today()

    basket: tuple[BasketMember, ...] = members(ctx.store, resolved_index, as_of_date)
    raw_rows: list[IndexConstituent] = [
        row for row in ctx.store.read("index_constituents") if row.index == resolved_index
    ]
    closes = _latest_close_by_underlying(
        ctx.store.read(
            "daily_bar",
            start_date=as_of_date - timedelta(days=7),
            end_date=as_of_date,
        ),
        as_of_date,
    )

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
