"""Risk router: read persisted portfolio risk aggregates and scenario PnL back.

Reads the ``risk_aggregates`` and ``scenario_results`` contracts and serializes the net
sensitivities / stress cells with provenance. The web Risk page renders the aggregates.
A missing partition or unknown portfolio returns an empty list, never a 500.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..context import AppContext
from ..serializers import risk_aggregate_to_dict, scenario_result_to_dict

router = APIRouter(prefix="/api/risk", tags=["risk"])


def _context(request: Request) -> AppContext:
    return request.app.state.ctx


@router.get("/portfolios")
def list_portfolios(request: Request) -> JSONResponse:
    """List the portfolio ids that have persisted risk aggregates."""
    ctx = _context(request)
    ids = sorted({row.portfolio_id for row in ctx.store.read("risk_aggregates")})
    return JSONResponse({"portfolios": ids})


@router.get("")
def get_risk(request: Request, portfolio_id: str | None = None) -> JSONResponse:
    """Return net-sensitivity aggregates, optionally filtered to one portfolio."""
    ctx = _context(request)
    rows = ctx.store.read("risk_aggregates")
    if portfolio_id is not None:
        rows = [row for row in rows if row.portfolio_id == portfolio_id]
    aggregates = [risk_aggregate_to_dict(row) for row in rows]
    return JSONResponse(
        {"portfolio_id": portfolio_id, "n_aggregates": len(aggregates), "aggregates": aggregates}
    )


@router.get("/scenarios")
def get_scenarios(request: Request, portfolio_id: str | None = None) -> JSONResponse:
    """Return stress-scenario PnL cells, optionally filtered to one portfolio."""
    ctx = _context(request)
    rows = ctx.store.read("scenario_results")
    if portfolio_id is not None:
        rows = [row for row in rows if row.portfolio_id == portfolio_id]
    cells = [scenario_result_to_dict(row) for row in rows]
    return JSONResponse(
        {"portfolio_id": portfolio_id, "n_cells": len(cells), "cells": cells}
    )
