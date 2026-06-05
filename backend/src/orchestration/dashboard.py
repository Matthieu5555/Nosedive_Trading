"""A small operator dashboard: is data flowing, building, passing QC, and current.

This is not a UI. It is a structured status object built from recorded state, plus a
plain-text renderer, so an operator (or a health endpoint) reads four answers at a
glance: is data flowing, are surfaces building, are QC checks passing, are scenario
reports current. Each answer is derived from durable facts — the run-state ledger, the
partitions on disk, the latest QC escalation, the live metric values — never from a
side effect, so the dashboard is a pure read and reproduces the same status for the
same state.

The two operational questions the spec calls out — *what was the last healthy run* and
*what is the current backlog* — are first-class fields, so they are answerable
instantly rather than reconstructed from logs.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .metrics import OrchestrationMetrics, sample_value
from .run_state import backlog_stages, last_healthy_trade_date

# The four headline health flags an operator scans first. A flag is "ok" when the
# evidence is present, "stale"/"missing" when it is not — never a bare boolean, so the
# reason is in the value.
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
    """The operator's at-a-glance status for one trade date, from recorded state.

    Every field is a derived fact: the four health flags, the events-flowing total, the
    last fully-healthy trade date, and the current backlog (the stages not yet finished
    cleanly for the date). It carries no clock and no I/O — it is what a renderer or a
    health endpoint serializes.
    """

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
        """True when all four headline flags are in their good state."""
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
    """Assemble the dashboard status for a trade date from recorded state.

    ``root_partitions`` is the underlyings expected to have data (e.g. snapshot
    partitions present); ``surface_partitions`` and ``scenario_partitions`` come from
    ``store.list_partitions`` for the surface and scenario tables. ``qc_status`` is the
    latest QC verdict for the date (``passing``/``failing``/``unknown``). ``metrics`` is
    read for the total events flowing. ``ledger_root`` is the store root holding the
    run-state ledger, from which the last healthy run and the backlog are read.

    Data is flowing when there is at least one events sample for the date's partitions;
    surfaces are building when a surface partition exists for each underlying that has
    raw data; scenarios are current when a scenario partition exists for the date. Each
    flag carries its own reason so the operator sees *why* something is not ok.
    """
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
    """Render a :class:`DashboardStatus` as a compact plain-text operator panel.

    Plain text, not HTML, because the dashboard's job is to be read fast in a terminal
    or a log. The last-healthy line and the backlog line are the two an operator looks
    at first, so they lead. Returns the panel as a single string.
    """
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
