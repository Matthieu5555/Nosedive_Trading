"""JSON-safe serialization for typed contracts at the presentation boundary.

Pure functions: no I/O, no clock, no mutation of inputs. Each function converts
one frozen contract dataclass to a plain dict with JSON-primitive values.
Provenance is carried through in a compact form (code version, config hash,
calc time, stamp hash) per the "provenance through to the UI" principle.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from algotrading.core import ProvenanceStamp
from algotrading.infra.contracts import (
    DailyBar,
    IndexConstituent,
    PricingResult,
    ProjectedOptionAnalytics,
    RiskAggregate,
    ScenarioResult,
    SurfaceParameters,
)
from algotrading.infra.pricing import UNIT_STRINGS
from algotrading.infra.risk import BasketRisk, LegRisk
from algotrading.infra.surfaces import SlicePlotSeries, degeneracy_reasons

if TYPE_CHECKING:
    # Used only as the annotation on dashboard_status_to_dict; imported under
    # TYPE_CHECKING to keep this module's runtime import surface to the contracts it
    # serializes. The body uses duck-typed attribute access, not the type.
    from algotrading.infra.orchestration import DashboardStatus


def _iso(value: datetime | date) -> str:
    """ISO-8601 string for a timezone-aware datetime or a date."""
    return value.isoformat()


def provenance_to_dict(stamp: ProvenanceStamp) -> dict[str, object]:
    """Compact, operator-facing provenance: the lineage handles, not every source ref."""
    return {
        "calc_ts": _iso(stamp.calc_ts),
        "code_version": stamp.code_version,
        "config_hashes": dict(stamp.config_hashes),
        "stamp_hash": stamp.stamp_hash,
        "n_sources": len(stamp.source_records),
    }


def surface_parameters_to_dict(row: SurfaceParameters) -> dict[str, object]:
    """Serialize one fitted SVI slice (parameters + fit diagnostics + provenance).

    The degeneracy facts travel with the slice: ``bound_hits``/``converged`` are passed
    through (``None`` for rows persisted before the fields existed — unknown, not clean),
    and the derived ``degenerate``/``degenerate_reasons`` flag applies the one policy home
    (:func:`~algotrading.infra.surfaces.degeneracy_reasons`) so a railed or arb-breached
    calibration is never served as if it were clean.
    """
    reasons = degeneracy_reasons(row.diagnostics)
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
            "bound_hits": (
                list(row.diagnostics.bound_hits)
                if row.diagnostics.bound_hits is not None
                else None
            ),
            "converged": row.diagnostics.converged,
        },
        "degenerate": bool(reasons),
        "degenerate_reasons": list(reasons),
        "source_snapshot_ts": _iso(row.source_snapshot_ts),
        "provenance": provenance_to_dict(row.provenance),
    }


def daily_bar_to_dict(row: DailyBar) -> dict[str, object]:
    """Serialize one daily OHLC bar — the candlestick chart's row (WS 1C/1E).

    Field names are the :class:`DailyBar` contract's, verbatim: ``trade_date`` plus
    ``open``/``high``/``low``/``close``/``volume``, with ``provider``/``bar_type``/``source``
    as lineage and a compact provenance stamp. A renamed OHLC field on the contract turns the
    field-name conformance assertion red.
    """
    return {
        "provider": row.provider,
        "underlying": row.underlying,
        "trade_date": _iso(row.trade_date),
        "open": row.open,
        "high": row.high,
        "low": row.low,
        "close": row.close,
        "volume": row.volume,
        "bar_type": row.bar_type,
        "source": row.source,
        "provenance": provenance_to_dict(row.provenance),
    }


def index_constituent_to_dict(row: IndexConstituent) -> dict[str, object]:
    """Serialize one bitemporal membership fact (WS 1A).

    Carries the constituent symbol, its as-of ``weight`` (nullable — labeled unavailable,
    never zeroed), and the half-open effective interval
    ``[effective_add_date, effective_remove_date)`` so the front can show when a name was in
    the basket. ``effective_remove_date`` is ``None`` for a current, never-removed member.
    """
    return {
        "index": row.index,
        "constituent": row.constituent,
        "weight": row.weight,
        "effective_add_date": _iso(row.effective_add_date),
        "effective_remove_date": (
            _iso(row.effective_remove_date) if row.effective_remove_date is not None else None
        ),
        "knowledge_date": _iso(row.knowledge_date),
        "vendor": row.vendor,
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
        "scenario_pnl": row.scenario_pnl,
        "scenario_version": row.scenario_version,
        "source_snapshot_ts": _iso(row.source_snapshot_ts),
        "provenance": provenance_to_dict(row.provenance),
    }


# The surface cells (WS 2B) are the cartesian (spot × vol) stress grid, persisted into the
# same ``scenario_results`` contract as the families cells and distinguished by this id
# prefix (see ``infra.risk.stress_surface._surface_scenario_id``). Selecting on it lets the
# surface reshape coexist with the families cell list — 2C reads the cells, 2B the surface.
_SURFACE_ID_PREFIX = "surf_"
# The full-reprice scenario PnL is a monetized account-currency amount, not a per-unit Greek,
# so it carries its own label rather than a ``UNIT_STRINGS`` Greek unit.
SCENARIO_PNL_UNIT = "$ (full-reprice PnL)"


def scenario_surface_to_dict(rows: list[ScenarioResult]) -> dict[str, object]:
    """Reshape the cartesian (spot × vol) surface cells into a Plotly-ready surface payload.

    Selects the ``surf_``-prefixed cells from ``rows`` (the cartesian grid 2B persists),
    sums ``scenario_pnl`` over contracts per ``(spot_shock, vol_shock)`` to the portfolio
    total, and arranges it as a z-grid aligned to the sorted shock axes — spot-major, so
    ``scenario_pnl[i][j]`` is the total under ``spot_shock[i]`` / ``vol_shock[j]``. A
    ``(spot, vol)`` combination with no persisted cell is a **labelled hole** — ``None`` in
    the z-grid plus the ``has_holes``/``n_holes`` flags — never a silent ``0.0``, which would
    read as a real "this stress costs nothing" quote (F-BFF-03). Field names follow ADR 0029
    (``spot_shock`` / ``vol_shock`` axes, ``scenario_pnl`` z-grid); the dollar PnL carries its
    ``unit`` string. An absent surface (no ``surf_`` cells, e.g. an unknown or empty basket)
    is a labelled empty surface — empty axes, HTTP 200 at the caller, never a 500.
    """
    surface_rows = [row for row in rows if row.scenario_id.startswith(_SURFACE_ID_PREFIX)]
    if not surface_rows:
        return {
            "spot_shock": [],
            "vol_shock": [],
            "scenario_pnl": [],
            "scenario_version": None,
            "unit": SCENARIO_PNL_UNIT,
            "n_cells": 0,
            "has_holes": False,
            "n_holes": 0,
        }
    spot_axis = sorted({row.spot_shock for row in surface_rows})
    vol_axis = sorted({row.vol_shock for row in surface_rows})
    totals: dict[tuple[float, float], float] = {}
    for row in surface_rows:
        key = (row.spot_shock, row.vol_shock)
        totals[key] = totals.get(key, 0.0) + row.scenario_pnl
    pnl_grid: list[list[float | None]] = [
        [totals.get((s, v)) for v in vol_axis] for s in spot_axis
    ]
    n_holes = sum(1 for grid_row in pnl_grid for value in grid_row if value is None)
    # One surface is one scenario version; if a stale mix is present, surface the smallest
    # deterministically rather than guessing (the cron rewrites a partition wholesale).
    versions = sorted({row.scenario_version for row in surface_rows})
    return {
        "spot_shock": spot_axis,
        "vol_shock": vol_axis,
        "scenario_pnl": pnl_grid,
        "scenario_version": versions[0],
        "unit": SCENARIO_PNL_UNIT,
        "n_cells": len(surface_rows),
        "has_holes": n_holes > 0,
        "n_holes": n_holes,
    }


def _metric(raw: float, value: float | None, unit: str) -> dict[str, object]:
    """One dollar metric for the front: the dollar value, its unit, and the raw per-unit Greek.

    The BFF metric contract (P0.2 / OQ-1, ADR 0036): a dollar number never crosses the
    boundary as a bare float — it carries the explicit ``unit`` it is quoted in and the
    ``raw`` per-unit Greek it derives from, so the front can label it and re-derive it.
    """
    return {"raw": raw, "dollar": value, "unit": unit}


def pricing_result_to_dict(row: PricingResult) -> dict[str, object]:
    """Serialize one contract's price, raw Greeks, and the unit-carrying dollar layer.

    Each ``dollar_*`` metric is emitted with the unit string of the pinned convention
    (gamma → "$ per 1% move", theta → "$ per calendar day") beside the raw per-unit Greek,
    so the front receives a labelled metric, not a bare float. ``dollar_theta`` /
    ``dollar_rho`` are additive-nullable; an older partition that lacks them serializes
    them as ``None`` dollar values rather than failing.

    The stored ``dollar_gamma`` is already in ``one_pct`` units (ADR 0036's canonical default:
    Γ·S²/100 per 1% move) — the pricing engine emits it that way. The BFF passes the value
    through unchanged under the matching ``one_pct`` unit label; no rescaling is needed here.
    """
    return {
        "snapshot_ts": _iso(row.snapshot_ts),
        "contract_key": row.contract_key,
        "pricer_version": row.pricer_version,
        "price": row.price,
        "metrics": {
            "delta": _metric(row.delta, row.dollar_delta, UNIT_STRINGS["dollar_delta"]),
            "gamma": _metric(
                row.gamma,
                row.dollar_gamma,
                UNIT_STRINGS["dollar_gamma_one_pct"],
            ),
            "vega": _metric(row.vega, row.dollar_vega, UNIT_STRINGS["dollar_vega"]),
            "theta": _metric(row.theta, row.dollar_theta, UNIT_STRINGS["dollar_theta_365"]),
            "rho": _metric(row.rho, row.dollar_rho, UNIT_STRINGS["dollar_rho"]),
        },
        "source_snapshot_ts": _iso(row.source_snapshot_ts),
        "provenance": provenance_to_dict(row.provenance),
    }


def _analytics_metric(raw: float, dollar: float | None, unit: str | None) -> dict[str, object]:
    """One dollar metric off a projected-analytics cell.

    The cell stores the dollar number *and* its unit string side by side (P0.2 / ADR 0036),
    so the BFF passes the stored unit straight through — it does not re-derive the convention.
    ``dollar_theta``/``dollar_rho`` and their units are additive-nullable; an older partition
    serializes them as ``None`` rather than failing.
    """
    return {"raw": raw, "dollar": dollar, "unit": unit}


def projected_option_analytics_to_dict(row: ProjectedOptionAnalytics) -> dict[str, object]:
    """Serialize one tenor × delta-band analytics cell (WS 1F) for the front.

    Field names follow ADR 0029: ``forward_price``, ``implied_vol``, ``log_moneyness``,
    ``dollar_*``. Each dollar Greek is emitted as a metric carrying the **unit string stored on
    the cell** (Delta\\$ per \\$1, Gamma\\$ per 1% move, Vega\\$ per vol point, Theta\\$ per
    calendar day, Rho\\$ per 1% rate) beside its raw per-unit Greek — the BFF tags the unit, it
    does not redefine or recompute it (the blueprint / 1F own that). The analytics router groups
    these by maturity into smile + surface-grid + dollar-Greek views.
    """
    return {
        "snapshot_ts": _iso(row.snapshot_ts),
        "provider": row.provider,
        "underlying": row.underlying,
        "tenor_label": row.tenor_label,
        "maturity_years": row.maturity_years,
        "delta_band": row.delta_band,
        "target_delta": row.target_delta,
        "log_moneyness": row.log_moneyness,
        "strike": row.strike,
        "forward_price": row.forward_price,
        "implied_vol": row.implied_vol,
        "total_variance": row.total_variance,
        "price": row.price,
        "metrics": {
            "delta": _analytics_metric(row.delta, row.dollar_delta, row.dollar_delta_unit),
            "gamma": _analytics_metric(row.gamma, row.dollar_gamma, row.dollar_gamma_unit),
            "vega": _analytics_metric(row.vega, row.dollar_vega, row.dollar_vega_unit),
            "theta": _analytics_metric(row.theta, row.dollar_theta, row.dollar_theta_unit),
            "rho": _analytics_metric(row.rho, row.dollar_rho, row.dollar_rho_unit),
        },
        "model_version": row.model_version,
        "pricer_version": row.pricer_version,
        "source_snapshot_ts": _iso(row.source_snapshot_ts),
        "provenance": provenance_to_dict(row.provenance),
    }


def _basket_metric(dollar: float | None, unit: str | None) -> dict[str, object]:
    """One aggregate basket dollar Greek: the summed dollar value and the unit it is quoted in.

    Unlike a per-contract metric there is no single ``raw`` per-unit Greek for a multi-leg sum,
    so the basket aggregate carries just ``dollar`` + ``unit``. A ``None`` dollar is a labelled
    unavailable Greek (an additive-nullable theta/rho missing on a contributing leg), not zero.
    """
    return {"dollar": dollar, "unit": unit}


def _basket_leg_to_dict(line: LegRisk) -> dict[str, object]:
    """One leg's signed contribution to each basket Greek, beside its cell context.

    This is the per-leg breakdown that proves the basket number is the sum of the Tab-1
    per-position dollar Greeks. ADR-0029 names (``dollar_*``, ``forward_price``, ``implied_vol``,
    ``log_moneyness``); the matched cell's context is echoed for resolved option legs (``None``
    for a stock or unresolved leg). An unresolved leg carries its ``gap_reason``.
    """
    leg = line.leg
    return {
        "instrument_kind": leg.instrument_kind,
        "side": leg.side,
        "quantity": leg.quantity,
        "underlying": leg.underlying,
        "tenor_label": leg.tenor_label,
        "delta_band": leg.delta_band,
        "resolved": line.resolved,
        "gap_reason": line.gap_reason,
        "forward_price": line.forward_price,
        "implied_vol": line.implied_vol,
        "log_moneyness": line.log_moneyness,
        "strike": line.strike,
        "price": line.price,
        "metrics": {
            "delta": _basket_metric(line.dollar_delta, line.dollar_delta_unit),
            "gamma": _basket_metric(line.dollar_gamma, line.dollar_gamma_unit),
            "vega": _basket_metric(line.dollar_vega, line.dollar_vega_unit),
            "theta": _basket_metric(line.dollar_theta, line.dollar_theta_unit),
            "rho": _basket_metric(line.dollar_rho, line.dollar_rho_unit),
        },
    }


def basket_risk_to_dict(result: BasketRisk) -> dict[str, object]:
    """Serialize a priced/risked multi-leg basket for the front (WS 2A).

    The basket's dollar Greeks are the **book-additive sum** of the per-position dollar Greeks
    WS-1F produced (``infra.risk.multileg.basket_risk`` — summation, never a recompute), each
    carried with its unit string (Delta\\$ per \\$1, Gamma\\$ per 1% move, Vega\\$ per vol point,
    Theta\\$ per calendar day, Rho\\$ per 1% rate) from the analytics rows. The per-leg
    ``legs`` breakdown carries each leg's signed contribution; ``gaps`` names every leg that
    could not be fully priced (unpriced cell, ambiguous provider, missing spot, unavailable
    theta/rho) — a labelled gap, never a silent zero, and HTTP 200 at the router, never a 500.
    """
    return {
        "basket_id": result.basket_id,
        "trade_date": result.trade_date.isoformat(),
        "underlying": result.underlying,
        "price": result.price,
        "metrics": {
            "delta": _basket_metric(result.dollar_delta, result.dollar_delta_unit),
            "gamma": _basket_metric(result.dollar_gamma, result.dollar_gamma_unit),
            "vega": _basket_metric(result.dollar_vega, result.dollar_vega_unit),
            "theta": _basket_metric(result.dollar_theta, result.dollar_theta_unit),
            "rho": _basket_metric(result.dollar_rho, result.dollar_rho_unit),
        },
        "legs": [_basket_leg_to_dict(line) for line in result.legs],
        "gaps": [
            {
                "underlying": gap.underlying,
                "tenor_label": gap.tenor_label,
                "delta_band": gap.delta_band,
                "reason": gap.reason,
            }
            for gap in result.gaps
        ],
        "n_legs": len(result.legs),
        "n_gaps": len(result.gaps),
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
    """Serialize the operator dashboard status (four flags + headline facts).

    Uses duck-typed attribute access so this works with whatever shape
    ``DashboardStatus`` takes when orchestration lands.
    """
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
