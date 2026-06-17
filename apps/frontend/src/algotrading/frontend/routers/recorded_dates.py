from __future__ import annotations

from datetime import date, datetime

from algotrading.infra.orchestration import backlog_stages, read_stage_runs
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..deps import CtxDep

router = APIRouter(prefix="/api/recorded-dates", tags=["recorded-dates"])


def _qc_verdict(stages: dict[str, str]) -> str:
    outcome = stages.get("qc")
    if outcome == "ok":
        return "pass"
    if outcome == "failed":
        return "fail"
    return "unknown"


@router.get("")
def get_recorded_dates(ctx: CtxDep, index: str | None = None) -> JSONResponse:
    resolved_index = index or ctx.default_underlying
    root = ctx.store_root

    stage_runs = read_stage_runs(root)

    # ONE canonical close per ``trade_date`` (ADR 0051 / blueprint §15 / 01-arch:17): the serving
    # view shows one settled close per day. Overwrite-last-wins means a same-day re-fetch replaces
    # the day's slot — there is no per-fetch ``run=`` selector. ``recorded_ts`` is the latest stage
    # timestamp banked for the date; the QC verdict is the date-level outcome.
    stages_by_date: dict[date, dict[str, str]] = {}
    recorded_by_date: dict[date, datetime] = {}
    for run in stage_runs:
        stages_by_date.setdefault(run.trade_date, {})[run.stage] = run.outcome
        if run.recorded_ts is not None:
            current = recorded_by_date.get(run.trade_date)
            if current is None or run.recorded_ts > current:
                recorded_by_date[run.trade_date] = run.recorded_ts
    all_dates = sorted(stages_by_date, reverse=True)
    complete = [d for d in all_dates if not backlog_stages(root, d)]

    available: list[dict[str, object]] = []
    for d in all_dates:
        if stages_by_date[d].get("analytics") != "ok":
            continue
        recorded_ts = recorded_by_date.get(d)
        available.append(
            {
                "date": d.isoformat(),
                "recorded_ts": recorded_ts.isoformat() if recorded_ts else None,
                "qc": _qc_verdict(stages_by_date[d]),
            }
        )

    return JSONResponse(
        {
            "index": resolved_index,
            "count": len(complete),
            "dates": [d.isoformat() for d in complete],
            "available": available,
        }
    )
