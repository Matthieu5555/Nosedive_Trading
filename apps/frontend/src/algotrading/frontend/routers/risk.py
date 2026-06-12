"""Risk router: read persisted portfolio risk aggregates and scenario PnL back.

Reads the ``risk_aggregates`` and ``scenario_results`` contracts and serializes the net
sensitivities / stress cells with provenance. The web Risk page renders the aggregates.
A missing partition or unknown portfolio returns an empty list, never a 500.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..deps import CtxDep
from ..serializers import (
    pricing_result_to_dict,
    risk_aggregate_to_dict,
    scenario_result_to_dict,
    scenario_surface_to_dict,
)

router = APIRouter(prefix="/api/risk", tags=["risk"])


@router.get("/portfolios")
def list_portfolios(ctx: CtxDep) -> JSONResponse:
    """List the portfolio ids that have persisted risk aggregates."""
    ids = sorted({row.portfolio_id for row in ctx.store.read("risk_aggregates")})
    return JSONResponse({"portfolios": ids})


@router.get("")
def get_risk(ctx: CtxDep, portfolio_id: str | None = None) -> JSONResponse:
    """Return net-sensitivity aggregates, optionally filtered to one portfolio."""
    rows = ctx.store.read("risk_aggregates")
    if portfolio_id is not None:
        rows = [row for row in rows if row.portfolio_id == portfolio_id]
    aggregates = [risk_aggregate_to_dict(row) for row in rows]
    return JSONResponse(
        {"portfolio_id": portfolio_id, "n_aggregates": len(aggregates), "aggregates": aggregates}
    )


@router.get("/metrics")
def get_metrics(ctx: CtxDep, underlying: str | None = None) -> JSONResponse:
    """Return per-contract price/Greeks with the unit-carrying dollar layer.

    Each dollar metric is read back with the explicit unit string of the pinned
    convention beside its raw per-unit Greek (the BFF metric contract, ADR 0036), so the
    front never receives a bare float. Optionally filtered to one underlying.
    """
    rows = ctx.store.read("pricing_results")
    if underlying is not None:
        rows = [row for row in rows if row.contract_key.split("|", 1)[0] == underlying]
    metrics = [pricing_result_to_dict(row) for row in rows]
    return JSONResponse({"underlying": underlying, "n_results": len(metrics), "results": metrics})


@router.get("/scenarios")
def get_scenarios(ctx: CtxDep, portfolio_id: str | None = None) -> JSONResponse:
    """Return stress-scenario PnL cells plus the reshaped (spot × vol) surface (WS 2B).

    Two additive views over the same persisted ``scenario_results`` rows: ``cells`` (the
    per-contract list, unchanged — 2C attributes over it) and ``surface`` (the cartesian
    ``surf_`` grid reshaped into axes + a ``scenario_pnl`` z-grid for the 2B Plotly page). A
    missing partition / unknown portfolio yields an empty list and a labelled empty surface,
    never a 500. Serving is read-only — the cron is the sole writer (ADR 0034).
    """
    rows = ctx.store.read("scenario_results")
    if portfolio_id is not None:
        rows = [row for row in rows if row.portfolio_id == portfolio_id]
    cells = [scenario_result_to_dict(row) for row in rows]
    surface = scenario_surface_to_dict(rows)
    return JSONResponse(
        {
            "portfolio_id": portfolio_id,
            "n_cells": len(cells),
            "cells": cells,
            "surface": surface,
        }
    )
