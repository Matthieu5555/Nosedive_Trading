"""Health router: the operator dashboard, wired to real recorded state.

Assembles ``orchestration.build_dashboard`` from the store's partitions (snapshots,
surfaces, scenarios), the latest QC verdict for the date, and the run-state ledger — so
the four health flags reflect what is actually on disk, not a hardcoded OK. The trade
date defaults to the most recent date with snapshot data.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from algotrading.infra.orchestration import build_dashboard, build_metrics
from algotrading.infra.orchestration.dashboard import QC_FAILING, QC_PASSING, QC_UNKNOWN
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..context import AppContext
from ..deps import CtxDep, TradeDateDep
from ..serializers import dashboard_status_to_dict
from ..store_reads import QC_FAIL_STATUSES, latest_partition_date

router = APIRouter(prefix="/api/health", tags=["health"])


def _qc_status_for(ctx: AppContext, trade_date: date) -> str:
    """Reduce the date's QC results to passing / failing / unknown."""
    rows = ctx.store.read("qc_results", trade_date=trade_date)
    if not rows:
        return QC_UNKNOWN
    if any(str(row.qc_status).lower() in QC_FAIL_STATUSES for row in rows):
        return QC_FAILING
    return QC_PASSING


@router.get("")
def get_health(ctx: CtxDep, trade_date: TradeDateDep) -> JSONResponse:
    """Return the operator dashboard status for a trade date (latest with data by default)."""
    snapshot_partitions = ctx.store.list_partitions("market_state_snapshots")
    surface_partitions = ctx.store.list_partitions("surface_parameters")
    scenario_partitions = ctx.store.list_partitions("scenario_results")

    resolved_date = trade_date or (
        latest_partition_date(snapshot_partitions)
        or latest_partition_date(surface_partitions)
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
