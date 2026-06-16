from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..deps import CtxDep, TradeDateDep
from ..serializers import surface_parameters_to_dict
from ..store_reads import read_for_underlying

router = APIRouter(prefix="/api/surfaces", tags=["surfaces"])


@router.get("/underlyings")
def list_surface_underlyings(ctx: CtxDep) -> JSONResponse:
    partitions = ctx.store.list_partitions("surface_parameters")
    underlyings = sorted({underlying for _, underlying in partitions})
    return JSONResponse({"underlyings": underlyings})


@router.get("")
def get_surface(
    ctx: CtxDep, trade_date: TradeDateDep, underlying: str | None = None
) -> JSONResponse:
    resolved_underlying = underlying or ctx.default_underlying
    rows = read_for_underlying(
        ctx.store, "surface_parameters", resolved_underlying, trade_date=trade_date
    )
    rows.sort(key=lambda row: row.maturity_years)
    slices = [surface_parameters_to_dict(row) for row in rows]
    return JSONResponse(
        {
            "underlying": resolved_underlying,
            "trade_date": trade_date.isoformat() if trade_date else None,
            "n_slices": len(slices),
            "slices": slices,
        }
    )
