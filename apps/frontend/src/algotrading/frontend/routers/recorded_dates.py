from __future__ import annotations

from datetime import date

from algotrading.infra.orchestration import backlog_stages, read_stage_runs
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..deps import CtxDep

router = APIRouter(prefix="/api/recorded-dates", tags=["recorded-dates"])


@router.get("")
def get_recorded_dates(ctx: CtxDep, index: str | None = None) -> JSONResponse:
    resolved_index = index or ctx.default_underlying
    root = ctx.store_root

    stages_by_date: dict[date, dict[str, str]] = {}
    for run in read_stage_runs(root):
        stages_by_date.setdefault(run.trade_date, {})[run.stage] = run.outcome
    all_dates = sorted(stages_by_date, reverse=True)

    complete = [d for d in all_dates if not backlog_stages(root, d)]

    def _qc_verdict(stages: dict[str, str]) -> str:
        outcome = stages.get("qc")
        if outcome == "ok":
            return "pass"
        if outcome == "failed":
            return "fail"
        return "unknown"

    available = [
        {"date": d.isoformat(), "qc": _qc_verdict(stages_by_date[d])}
        for d in all_dates
        if stages_by_date[d].get("analytics") == "ok"
    ]
    return JSONResponse(
        {
            "index": resolved_index,
            "count": len(complete),
            "dates": [d.isoformat() for d in complete],
            "available": available,
        }
    )
