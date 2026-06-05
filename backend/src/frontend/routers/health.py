"""Health router: the operator dashboard, wired to real recorded state.

Assembles ``orchestration.build_dashboard`` from the store's partitions (snapshots,
surfaces, scenarios), the latest QC verdict for the date, and the run-state ledger — so
the four health flags reflect what is actually on disk, not a hardcoded OK. The trade
date defaults to the most recent date with snapshot data.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from orchestration import build_dashboard, build_metrics
from orchestration.dashboard import QC_FAILING, QC_PASSING, QC_UNKNOWN

from ..context import AppContext
from ..serializers import dashboard_status_to_dict

router = APIRouter(prefix="/api/health", tags=["health"])

# QC result statuses that mean a check failed (lower-cased before comparison).
_QC_FAIL_STATUSES = frozenset({"fail", "failing", "failed", "reject", "error"})


def _context(request: Request) -> AppContext:
    return request.app.state.ctx


def _latest_partition_date(partitions: list[tuple[date, str]]) -> date | None:
    return max((part_date for part_date, _ in partitions), default=None)


def _qc_status_for(ctx: AppContext, trade_date: date) -> str:
    """Reduce the date's QC results to passing / failing / unknown."""
    rows = ctx.store.read("qc_results", trade_date=trade_date)
    if not rows:
        return QC_UNKNOWN
    if any(str(row.status).lower() in _QC_FAIL_STATUSES for row in rows):
        return QC_FAILING
    return QC_PASSING


@router.get("")
def get_health(request: Request, trade_date: str | None = None) -> JSONResponse:
    """Return the operator dashboard status for a trade date (latest with data by default)."""
    ctx = _context(request)
    snapshot_partitions = ctx.store.list_partitions("market_state_snapshots")
    surface_partitions = ctx.store.list_partitions("surface_parameters")
    scenario_partitions = ctx.store.list_partitions("scenario_results")

    if trade_date is not None:
        try:
            resolved_date = date.fromisoformat(trade_date)
        except ValueError:
            return JSONResponse(
                {"error": "bad_trade_date", "trade_date": trade_date}, status_code=400
            )
    else:
        resolved_date = (
            _latest_partition_date(snapshot_partitions)
            or _latest_partition_date(surface_partitions)
            or datetime.now(tz=UTC).date()
        )

    status = build_dashboard(
        root_partitions=snapshot_partitions,
        surface_partitions=surface_partitions,
        scenario_partitions=scenario_partitions,
        trade_date=resolved_date,
        qc_status=_qc_status_for(ctx, resolved_date),
        metrics=build_metrics(),
        ledger_root=ctx.store_root,
    )
    return JSONResponse(dashboard_status_to_dict(status))
