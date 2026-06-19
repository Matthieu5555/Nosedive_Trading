from __future__ import annotations

from datetime import date, datetime

from algotrading.infra.orchestration import backlog_stages, read_stage_runs, runs_for
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
    # the day's DATA in place. ``recorded_ts`` is the latest stage timestamp banked for the date;
    # the QC verdict is the date-level outcome.
    #
    # Each entry also carries the capture IDENTITY: ``run_id`` is the latest capture banked for the
    # date (the one whose data the store serves), and ``runs`` lists every same-day capture newest
    # first. This is additive — the date stays the canonical close, but the read path can now name
    # the exact capture so picking a fresh same-day re-fetch produces a distinct request, instead of
    # an identical date-only URL the cache never refetches. Older runs are addressable identities;
    # their data was overwritten, so a store read with a stale run_id raises StaleRunError.
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
        day_runs = runs_for(root, d)
        available.append(
            {
                "date": d.isoformat(),
                "recorded_ts": recorded_ts.isoformat() if recorded_ts else None,
                "qc": _qc_verdict(stages_by_date[d]),
                "run_id": day_runs[0].run_id if day_runs else None,
                "runs": [
                    {"run_id": r.run_id, "recorded_ts": r.recorded_ts.isoformat()}
                    for r in day_runs
                ],
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
