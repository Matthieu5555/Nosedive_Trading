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

from algotrading.infra.orchestration import backlog_stages, read_stage_runs
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..context import AppContext

router = APIRouter(prefix="/api/recorded-dates", tags=["recorded-dates"])


def _context(request: Request) -> AppContext:
    return request.app.state.ctx


@router.get("")
def get_recorded_dates(request: Request, index: str | None = None) -> JSONResponse:
    """Return the completed, gap-free trade dates (and their count) from the run ledger.

    Only dates whose full EOD sequence finished cleanly are returned, newest first. A
    partial/failed run (a stage missing or recorded ``failed``) is excluded, which is exactly
    what makes the counter the operator-facing face of capture coverage. An empty ledger yields
    ``count == 0`` and an empty ``dates`` list.
    """
    ctx = _context(request)
    resolved_index = index or ctx.default_underlying
    root = ctx.store_root
    all_dates = sorted({run.trade_date for run in read_stage_runs(root)}, reverse=True)
    complete = [d for d in all_dates if not backlog_stages(root, d)]
    return JSONResponse(
        {
            "index": resolved_index,
            "count": len(complete),
            "dates": [d.isoformat() for d in complete],
        }
    )
