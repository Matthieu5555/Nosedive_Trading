from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from algotrading.infra.orchestration import backlog_stages, read_stage_runs
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..deps import CtxDep

router = APIRouter(prefix="/api/recorded-dates", tags=["recorded-dates"])

# The fetch's analytics live here; its ``run=`` partitions are what make a fetch addressable, so
# this is the table we ask "which runs actually have data on disk for this date".
_ANALYTICS_TABLE = "projected_option_analytics"


@dataclass
class _RunLedger:
    """A single fire's stage outcomes and when it landed, gathered from the run-state ledger."""

    trade_date: date
    stages: dict[str, str] = field(default_factory=dict)
    recorded_ts: datetime | None = None


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
    store = ctx.store

    stage_runs = read_stage_runs(root)

    # Date-level view (unchanged contract): which trade dates fully completed every stage, used for
    # ``dates``/``count`` and as the QC verdict for legacy flat data with no per-run handle.
    stages_by_date: dict[date, dict[str, str]] = {}
    for run in stage_runs:
        stages_by_date.setdefault(run.trade_date, {})[run.stage] = run.outcome
    all_dates = sorted(stages_by_date, reverse=True)
    complete = [d for d in all_dates if not backlog_stages(root, d)]

    # Per-run view: each fire (run_id) carries its own stage outcomes and the wall-clock time it
    # landed (the latest stage timestamp). This is how one trade date can hold several fetches.
    runs: dict[str, _RunLedger] = {}
    for run in stage_runs:
        ledger = runs.setdefault(run.run_id, _RunLedger(trade_date=run.trade_date))
        ledger.stages[run.stage] = run.outcome
        if ledger.recorded_ts is None or run.recorded_ts > ledger.recorded_ts:
            ledger.recorded_ts = run.recorded_ts

    # ``available`` is one entry per selectable fetch, newest-first. For a date whose analytics are
    # run-partitioned on disk, we emit one entry per ``run=`` partition (its run_id + the minute it
    # landed); for legacy flat data — no ``run=`` partition to address — we emit a single date-only
    # entry (run_id null) so the picker still offers the date without dangling on a missing run.
    available: list[dict[str, object]] = []
    for d in all_dates:
        if stages_by_date[d].get("analytics") != "ok":
            continue
        on_disk = store.runs_for(_ANALYTICS_TABLE, d)
        if on_disk:
            for run_id in on_disk:
                ledger = runs.get(run_id)
                recorded_ts = ledger.recorded_ts if ledger else None
                available.append(
                    {
                        "date": d.isoformat(),
                        "run_id": run_id,
                        "recorded_ts": recorded_ts.isoformat() if recorded_ts else None,
                        "qc": _qc_verdict(ledger.stages) if ledger else "unknown",
                    }
                )
        else:
            available.append(
                {
                    "date": d.isoformat(),
                    "run_id": None,
                    "recorded_ts": None,
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
