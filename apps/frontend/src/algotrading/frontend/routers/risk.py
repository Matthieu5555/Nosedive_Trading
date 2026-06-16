from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..deps import CtxDep
from ..serializers import (
    named_scenarios_to_list,
    pricing_result_to_dict,
    rate_scenarios_to_list,
    risk_aggregate_to_dict,
    scenario_result_to_dict,
    scenario_surface_to_dict,
)

router = APIRouter(prefix="/api/risk", tags=["risk"])


@router.get("/portfolios")
def list_portfolios(ctx: CtxDep) -> JSONResponse:
    ids = sorted({row.portfolio_id for row in ctx.store.read("risk_aggregates")})
    return JSONResponse({"portfolios": ids})


@router.get("")
def get_risk(ctx: CtxDep, portfolio_id: str | None = None) -> JSONResponse:
    rows = ctx.store.read("risk_aggregates")
    if portfolio_id is not None:
        rows = [row for row in rows if row.portfolio_id == portfolio_id]
    aggregates = [risk_aggregate_to_dict(row) for row in rows]
    return JSONResponse(
        {"portfolio_id": portfolio_id, "n_aggregates": len(aggregates), "aggregates": aggregates}
    )


@router.get("/metrics")
def get_metrics(ctx: CtxDep, underlying: str | None = None) -> JSONResponse:
    rows = ctx.store.read("pricing_results")
    if underlying is not None:
        rows = [row for row in rows if row.contract_key.split("|", 1)[0] == underlying]
    metrics = [pricing_result_to_dict(row) for row in rows]
    return JSONResponse({"underlying": underlying, "n_results": len(metrics), "results": metrics})


@router.get("/scenarios")
def get_scenarios(ctx: CtxDep, portfolio_id: str | None = None) -> JSONResponse:
    rows = ctx.store.read("scenario_results")
    if portfolio_id is not None:
        rows = [row for row in rows if row.portfolio_id == portfolio_id]
    cells = [scenario_result_to_dict(row) for row in rows]
    surface = scenario_surface_to_dict(rows)
    named = named_scenarios_to_list(rows)
    rate = rate_scenarios_to_list(rows)
    return JSONResponse(
        {
            "portfolio_id": portfolio_id,
            "n_cells": len(cells),
            "cells": cells,
            "surface": surface,
            "named": named,
            "n_named": len(named),
            "rate": rate,
            "n_rate": len(rate),
        }
    )
