from __future__ import annotations

from datetime import UTC, datetime

from algotrading.execution import JsonlFillsLedger
from algotrading.infra.contracts import PricingResult
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..context import AppContext
from ..deps import CtxDep, TradeDateDep
from ..positions_read import booked_position_book, fills_view
from ..serializers import position_book_to_dict

router = APIRouter(prefix="/api/positions", tags=["positions"])

_BOOKING_DIRNAME = "booking"
_FILLS_FILENAME = "fills.jsonl"


def _ledger(ctx: AppContext) -> JsonlFillsLedger:
    return JsonlFillsLedger(ctx.store_root / _BOOKING_DIRNAME / _FILLS_FILENAME)


def _pricing_rows(ctx: AppContext) -> list[PricingResult]:
    return list(ctx.store.read("pricing_results"))


@router.get("/fills")
def list_fills(
    ctx: CtxDep, trade_date: TradeDateDep, underlying: str | None = None
) -> JSONResponse:
    ledger = _ledger(ctx)
    fills = ledger.read(trade_date=trade_date, underlying=underlying)
    rows = fills_view(fills)
    return JSONResponse(
        {
            "trade_date": None if trade_date is None else trade_date.isoformat(),
            "underlying": underlying,
            "n_fills": len(rows),
            "fills": list(rows),
        }
    )


@router.get("")
def get_positions(
    ctx: CtxDep, trade_date: TradeDateDep, underlying: str | None = None
) -> JSONResponse:
    ledger = _ledger(ctx)
    book = booked_position_book(
        ledger,
        _pricing_rows(ctx),
        source_ts=datetime.now(UTC),
        trade_date=trade_date,
        underlying=underlying,
    )
    return JSONResponse(position_book_to_dict(book))
