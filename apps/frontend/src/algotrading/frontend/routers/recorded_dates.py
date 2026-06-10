"""Recorded-dates router: the operator face of capture coverage (WS 1G/1H).

Returns, for the chosen index, the trade dates with a **completed, gap-free** end-of-day run
plus the count — sourced from the **1G run-state ledger**, which distinguishes a complete run
from a partial/failed one, never a raw partition listing (a partition can exist for a
partially-captured day). A date is "recorded" only when every canonical EOD stage recorded a
clean completion (``backlog_stages`` empty); a partial or failed run is excluded. The front
shows an "N days recorded" counter and a date dropdown that drives the page's ``as_of``;
selecting a returned past date re-resolves the constituent list and analytics as of that date.
An empty / not-yet-captured ledger yields ``count == 0`` with a labeled empty state, never a
500.

The run-state ledger is operational bookkeeping keyed by ``(trade_date, stage)`` and is not
itself index-scoped; the ``index`` parameter is carried through so the front can drive the
as-of re-resolution for that index off the returned dates.
"""

from __future__ import annotations

from datetime import date

from algotrading.infra.orchestration import backlog_stages, read_stage_runs
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..context import AppContext

router = APIRouter(prefix="/api/recorded-dates", tags=["recorded-dates"])


def _context(request: Request) -> AppContext:
    return request.app.state.ctx


@router.get("")
def get_recorded_dates(request: Request, index: str | None = None) -> JSONResponse:
    """Return the trade dates the front can show, plus the clean-coverage count.

    ``dates``/``count`` are the **qc-clean, gap-free** days (every canonical EOD stage finished
    cleanly) — the operator-facing coverage figure, unchanged. ``available`` is the broader set
    the date picker actually offers: every day whose ``analytics`` stage produced a surface,
    **including qc-failing ones**, each tagged with its QC verdict (``pass``/``fail``/``unknown``).
    A degraded day stays selectable and is shown with its QC badge rather than hidden (cahier des
    charges §3.1 QC badge + §5 "show degraded states, don't mask them"). An empty ledger yields
    ``count == 0`` and empty lists.
    """
    ctx = _context(request)
    resolved_index = index or ctx.default_underlying
    root = ctx.store_root

    # Per-date stage outcomes, so we can report both the clean coverage AND the viewable
    # (possibly qc-failing) days with their QC verdict.
    stages_by_date: dict[date, dict[str, str]] = {}
    for run in read_stage_runs(root):
        stages_by_date.setdefault(run.trade_date, {})[run.stage] = run.outcome
    all_dates = sorted(stages_by_date, reverse=True)

    complete = [d for d in all_dates if not backlog_stages(root, d)]

    def _qc_verdict(stages: dict[str, str]) -> str:
        # "qc"/"ok"/"failed" are the ledger's stable stage/outcome keys (run_state.py).
        outcome = stages.get("qc")
        if outcome == "ok":
            return "pass"
        if outcome == "failed":
            return "fail"
        return "unknown"  # the qc stage never recorded (an incomplete run)

    # Viewable days: ``analytics`` produced a surface, regardless of the QC verdict — so a
    # degraded (qc-failing) but complete day stays SELECTABLE, shown with its QC badge.
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
