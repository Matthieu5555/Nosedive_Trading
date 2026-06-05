"""JSON-safe serialization for our typed contracts at the presentation boundary.

Each function converts one frozen contract dataclass to a plain dict with only
JSON-primitive values (str, int, float, bool, None, list, dict). ``datetime``/``date``
fields become ISO-8601 strings. Provenance is carried through in a compact form — the
determinism handles an operator actually reads (code version, config hash, calc time,
stamp hash) — per M8's "provenance through to the UI where it aids the operator".

Pure functions: no I/O, no clock, no mutation of the inputs.
"""

from __future__ import annotations

from datetime import date, datetime

from contracts import RiskAggregate, ScenarioResult, SurfaceParameters
from orchestration import DashboardStatus
from provenance import ProvenanceStamp
from surfaces import SlicePlotSeries


def _iso(value: datetime | date) -> str:
    """ISO-8601 string for a timezone-aware datetime or a date."""
    return value.isoformat()


def provenance_to_dict(stamp: ProvenanceStamp) -> dict[str, object]:
    """Compact, operator-facing provenance: the lineage handles, not every source ref."""
    return {
        "calc_ts": _iso(stamp.calc_ts),
        "code_version": stamp.code_version,
        "config_hash": stamp.config_hash,
        "stamp_hash": stamp.stamp_hash,
        "n_sources": len(stamp.source_records),
    }


def surface_parameters_to_dict(row: SurfaceParameters) -> dict[str, object]:
    """Serialize one fitted SVI slice (parameters + fit diagnostics + provenance)."""
    return {
        "snapshot_ts": _iso(row.snapshot_ts),
        "underlying": row.underlying,
        "maturity_years": row.maturity_years,
        "model_version": row.model_version,
        "svi_a": row.svi_a,
        "svi_b": row.svi_b,
        "svi_rho": row.svi_rho,
        "svi_m": row.svi_m,
        "svi_sigma": row.svi_sigma,
        "expiry_date": _iso(row.expiry_date),
        "day_count": row.day_count,
        "diagnostics": {
            "rmse": row.diagnostics.rmse,
            "n_points": row.diagnostics.n_points,
            "arb_free": row.diagnostics.arb_free,
        },
        "source_snapshot_ts": _iso(row.source_snapshot_ts),
        "provenance": provenance_to_dict(row.provenance),
    }


def risk_aggregate_to_dict(row: RiskAggregate) -> dict[str, object]:
    """Serialize one portfolio group's net sensitivities."""
    return {
        "valuation_ts": _iso(row.valuation_ts),
        "portfolio_id": row.portfolio_id,
        "group_key": row.group_key,
        "net_delta": row.net_delta,
        "net_gamma": row.net_gamma,
        "net_vega": row.net_vega,
        "net_theta": row.net_theta,
        "source_snapshot_ts": _iso(row.source_snapshot_ts),
        "provenance": provenance_to_dict(row.provenance),
    }


def scenario_result_to_dict(row: ScenarioResult) -> dict[str, object]:
    """Serialize one stress-scenario PnL cell."""
    return {
        "valuation_ts": _iso(row.valuation_ts),
        "portfolio_id": row.portfolio_id,
        "scenario_id": row.scenario_id,
        "contract_key": row.contract_key,
        "spot_shock": row.spot_shock,
        "vol_shock": row.vol_shock,
        "time_shock": row.time_shock,
        "pnl": row.pnl,
        "scenario_version": row.scenario_version,
        "source_snapshot_ts": _iso(row.source_snapshot_ts),
        "provenance": provenance_to_dict(row.provenance),
    }


def slice_plot_series_to_dict(series: SlicePlotSeries) -> dict[str, object]:
    """Serialize a fitted smile's raw-vs-fitted plot series (log-moneyness grid)."""
    return {
        "raw_k": list(series.raw_k),
        "raw_w": list(series.raw_w),
        "grid_k": list(series.grid_k),
        "fitted_w": list(series.fitted_w),
    }


def dashboard_status_to_dict(status: DashboardStatus) -> dict[str, object]:
    """Serialize the operator dashboard status (the four flags + headline facts)."""
    return {
        "trade_date": _iso(status.trade_date),
        "data_flowing": status.data_flowing,
        "surfaces_building": status.surfaces_building,
        "qc_status": status.qc_status,
        "scenarios_current": status.scenarios_current,
        "events_total": status.events_total,
        "last_healthy_trade_date": (
            _iso(status.last_healthy_trade_date)
            if status.last_healthy_trade_date is not None
            else None
        ),
        "backlog": list(status.backlog),
        "is_healthy": status.is_healthy,
    }
