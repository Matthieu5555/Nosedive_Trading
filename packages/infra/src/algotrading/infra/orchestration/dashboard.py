from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .metrics import OrchestrationMetrics, sample_value
from .run_state import backlog_stages, last_healthy_trade_date

FLOWING_OK = "ok"
FLOWING_NO_DATA = "no_data"
BUILDING_OK = "ok"
BUILDING_MISSING = "missing"
QC_PASSING = "passing"
QC_FAILING = "failing"
QC_UNKNOWN = "unknown"
CURRENT_OK = "current"
CURRENT_STALE = "stale"


@dataclass(frozen=True, slots=True)
class DashboardStatus:

    trade_date: date
    data_flowing: str
    surfaces_building: str
    qc_status: str
    scenarios_current: str
    events_total: float
    last_healthy_trade_date: date | None
    backlog: tuple[str, ...]

    @property
    def is_healthy(self) -> bool:
        return (
            self.data_flowing == FLOWING_OK
            and self.surfaces_building == BUILDING_OK
            and self.qc_status == QC_PASSING
            and self.scenarios_current == CURRENT_OK
        )


def build_dashboard(
    *,
    root_partitions: Sequence[tuple[date, str]],
    surface_partitions: Sequence[tuple[date, str]],
    scenario_partitions: Sequence[tuple[date, str]],
    trade_date: date,
    qc_status: str,
    metrics: OrchestrationMetrics,
    ledger_root: Path,
) -> DashboardStatus:
    root = Path(ledger_root)
    underlyings_with_data = {
        underlying for part_date, underlying in root_partitions if part_date == trade_date
    }
    events_total = sum(
        sample_value(metrics.registry, "events_collected_total", {"underlying": underlying})
        for underlying in underlyings_with_data
    )

    data_flowing = FLOWING_OK if underlyings_with_data else FLOWING_NO_DATA

    surface_underlyings = {
        underlying for part_date, underlying in surface_partitions if part_date == trade_date
    }
    surfaces_building = (
        BUILDING_OK
        if underlyings_with_data and underlyings_with_data <= surface_underlyings
        else BUILDING_MISSING
    )

    scenario_present = any(part_date == trade_date for part_date, _ in scenario_partitions)
    scenarios_current = CURRENT_OK if scenario_present else CURRENT_STALE

    return DashboardStatus(
        trade_date=trade_date,
        data_flowing=data_flowing,
        surfaces_building=surfaces_building,
        qc_status=qc_status,
        scenarios_current=scenarios_current,
        events_total=events_total,
        last_healthy_trade_date=last_healthy_trade_date(root),
        backlog=tuple(backlog_stages(root, trade_date)),
    )


def render_dashboard(status: DashboardStatus) -> str:
    last_healthy = (
        status.last_healthy_trade_date.isoformat()
        if status.last_healthy_trade_date is not None
        else "none"
    )
    backlog = ", ".join(status.backlog) if status.backlog else "none"
    lines = [
        f"=== orchestration status: {status.trade_date.isoformat()} ===",
        f"last healthy run : {last_healthy}",
        f"backlog          : {backlog}",
        f"data flowing     : {status.data_flowing} ({status.events_total:g} events)",
        f"surfaces building: {status.surfaces_building}",
        f"qc               : {status.qc_status}",
        f"scenarios current: {status.scenarios_current}",
        f"overall          : {'healthy' if status.is_healthy else 'attention'}",
    ]
    return "\n".join(lines)
