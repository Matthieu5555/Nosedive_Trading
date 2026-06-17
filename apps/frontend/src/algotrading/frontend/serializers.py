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
    ScenarioAttribution,
    ScenarioResult,
    StrategySignal,
    SurfaceParameters,
)
from algotrading.infra.orders import Limit, OrderTicket
from algotrading.infra.pricing import UNIT_STRINGS
from algotrading.infra.risk import BasketRisk, LegRisk
from algotrading.infra.surfaces import DenseSurface, SlicePlotSeries, degeneracy_reasons

from .basket_scenarios import BasketStressResult
from .positions_read import GreekComponent, PositionBook, PositionLine

if TYPE_CHECKING:
    from algotrading.infra.orchestration import DashboardStatus


def _iso(value: datetime | date) -> str:
    return value.isoformat()


def provenance_to_dict(stamp: ProvenanceStamp) -> dict[str, object]:
    return {
        "calc_ts": _iso(stamp.calc_ts),
        "code_version": stamp.code_version,
        "config_hashes": dict(stamp.config_hashes),
        "stamp_hash": stamp.stamp_hash,
        "n_sources": len(stamp.source_records),
    }


def surface_parameters_to_dict(row: SurfaceParameters) -> dict[str, object]:
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


def dense_surface_to_dict(surface: DenseSurface) -> dict[str, object]:
    return {
        "log_moneyness": list(surface.log_moneyness),
        "maturity_years": list(surface.maturity_years),
        "implied_vol": [list(row) for row in surface.implied_vol],
        "model_version": surface.model_version,
        "degenerate_maturity_years": list(surface.degenerate_maturity_years),
    }


def daily_bar_to_dict(row: DailyBar) -> dict[str, object]:
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
    return {
        "valuation_ts": _iso(row.valuation_ts),
        "portfolio_id": row.portfolio_id,
        "scenario_id": row.scenario_id,
        "contract_key": row.contract_key,
        "spot_shock": row.spot_shock,
        "vol_shock": row.vol_shock,
        "time_shock": row.time_shock,
        "rate_shock": row.rate_shock,
        "scenario_pnl": row.scenario_pnl,
        "scenario_version": row.scenario_version,
        "source_snapshot_ts": _iso(row.source_snapshot_ts),
        "provenance": provenance_to_dict(row.provenance),
    }


_SURFACE_ID_PREFIX = "surf_"
SCENARIO_PNL_UNIT = "$ (full-reprice PnL)"


def scenario_surface_to_dict(rows: list[ScenarioResult]) -> dict[str, object]:
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


_NAMED_ID_PREFIX = "named_"


def _named_label(scenario_id: str) -> str:
    return scenario_id[len(_NAMED_ID_PREFIX) :]


def named_scenarios_to_list(rows: list[ScenarioResult]) -> list[dict[str, object]]:
    named_rows = [row for row in rows if row.scenario_id.startswith(_NAMED_ID_PREFIX)]
    totals: dict[str, float] = {}
    leg_counts: dict[str, int] = {}
    seed: dict[str, ScenarioResult] = {}
    for row in named_rows:
        totals[row.scenario_id] = totals.get(row.scenario_id, 0.0) + row.scenario_pnl
        leg_counts[row.scenario_id] = leg_counts.get(row.scenario_id, 0) + 1
        seed.setdefault(row.scenario_id, row)
    return [
        {
            "scenario_id": scenario_id,
            "label": _named_label(scenario_id),
            "spot_shock": seed[scenario_id].spot_shock,
            "vol_shock": seed[scenario_id].vol_shock,
            "rate_shock": seed[scenario_id].rate_shock,
            "scenario_pnl": totals[scenario_id],
            "scenario_version": seed[scenario_id].scenario_version,
            "n_legs": leg_counts[scenario_id],
            "unit": SCENARIO_PNL_UNIT,
        }
        for scenario_id in sorted(totals)
    ]


_RATE_ID_PREFIX = "rate_"
RATE_SHOCK_BP_UNIT = "bp"
_BP_PER_UNIT_RATE = 10_000.0


def _rate_shock_of(row: ScenarioResult) -> float:
    return row.rate_shock if row.rate_shock is not None else 0.0


def rate_scenarios_to_list(rows: list[ScenarioResult]) -> list[dict[str, object]]:
    rate_rows = [row for row in rows if row.scenario_id.startswith(_RATE_ID_PREFIX)]
    totals: dict[str, float] = {}
    leg_counts: dict[str, int] = {}
    seed: dict[str, ScenarioResult] = {}
    for row in rate_rows:
        totals[row.scenario_id] = totals.get(row.scenario_id, 0.0) + row.scenario_pnl
        leg_counts[row.scenario_id] = leg_counts.get(row.scenario_id, 0) + 1
        seed.setdefault(row.scenario_id, row)
    ordered_ids = sorted(totals, key=lambda scenario_id: _rate_shock_of(seed[scenario_id]))
    return [
        {
            "scenario_id": scenario_id,
            "rate_shock": _rate_shock_of(seed[scenario_id]),
            "bp": _rate_shock_of(seed[scenario_id]) * _BP_PER_UNIT_RATE,
            "scenario_pnl": totals[scenario_id],
            "scenario_version": seed[scenario_id].scenario_version,
            "n_legs": leg_counts[scenario_id],
            "unit": SCENARIO_PNL_UNIT,
            "bp_unit": RATE_SHOCK_BP_UNIT,
        }
        for scenario_id in ordered_ids
    ]


ATTRIBUTION_TERM_UNIT = "$ (PnL contribution)"
ATTRIBUTION_RESIDUAL_UNIT = "$ (residual vs full reprice)"

_ATTRIBUTION_TERMS: tuple[tuple[str, str], ...] = (
    ("Delta", "delta_pnl"),
    ("Gamma", "gamma_pnl"),
    ("Vega", "vega_pnl"),
    ("Theta", "theta_pnl"),
)

# Second-order PnL contributions. The compute layer carries these as nullable columns on
# ScenarioAttribution; they reach the payload only when the engine actually decomposed them,
# so a record that predates the second-order terms still serializes its four-term waterfall
# unchanged. The dPnL decomposition stops at Volga — Charm is a display Greek, never a term.
_SECOND_ORDER_ATTRIBUTION_TERMS: tuple[tuple[str, str], ...] = (
    ("Rho", "rho_pnl"),
    ("Vanna", "vanna_pnl"),
    ("Volga", "volga_pnl"),
)


def scenario_attribution_to_dict(row: ScenarioAttribution) -> dict[str, object]:
    terms = [
        {
            "name": name,
            "dollars": getattr(row, field),
            "unit": ATTRIBUTION_TERM_UNIT,
        }
        for name, field in _ATTRIBUTION_TERMS
    ]
    terms.extend(
        {
            "name": name,
            "dollars": getattr(row, field),
            "unit": ATTRIBUTION_TERM_UNIT,
        }
        for name, field in _SECOND_ORDER_ATTRIBUTION_TERMS
        if getattr(row, field) is not None
    )
    return {
        "valuation_ts": _iso(row.valuation_ts),
        "portfolio_id": row.portfolio_id,
        "scenario_id": row.scenario_id,
        "contract_key": row.contract_key,
        "level": row.level,
        "terms": terms,
        "residual": {"dollars": row.residual, "unit": ATTRIBUTION_RESIDUAL_UNIT},
        "verdict": {
            "within_tolerance": row.within_tolerance,
            "residual_abs_tol": row.residual_abs_tol,
            "residual_rel_tol": row.residual_rel_tol,
        },
        "approx_pnl": row.approx_pnl,
        "full_reprice_pnl": row.full_reprice_pnl,
        "residual_abs_tol": row.residual_abs_tol,
        "residual_rel_tol": row.residual_rel_tol,
        "scenario_version": row.scenario_version,
        "attribution_version": row.attribution_version,
        "source_snapshot_ts": _iso(row.source_snapshot_ts),
        "provenance": provenance_to_dict(row.provenance),
    }


SIGNAL_KIND_IV_RANK = "iv_rank"
SIGNAL_KIND_IV_VS_REALIZED = "iv_vs_realized"
SIGNAL_KIND_TERM_STRUCTURE_SLOPE = "term_structure_slope"
SIGNAL_KIND_IMPLIED_CORRELATION = "implied_correlation"

SIGNAL_DISPLAY: dict[str, tuple[str, str]] = {
    SIGNAL_KIND_IV_RANK: ("IV rank", "fraction [0,1]"),
    SIGNAL_KIND_IV_VS_REALIZED: ("Realized − implied", "vol points (annualized)"),
    SIGNAL_KIND_TERM_STRUCTURE_SLOPE: ("Term-structure slope", "vol points (back − front)"),
    SIGNAL_KIND_IMPLIED_CORRELATION: ("Implied correlation ρ̄", "correlation [-1,1]"),
}


def strategy_signal_to_dict(row: StrategySignal) -> dict[str, object]:
    label, unit = SIGNAL_DISPLAY.get(row.signal_kind, (row.signal_kind, None))
    return {
        "signal_kind": row.signal_kind,
        "label": label,
        "subject": row.subject,
        "tenor_label": row.tenor_label,
        "value": row.value,
        "unit": unit,
        "snapshot_ts": _iso(row.snapshot_ts),
        "source_snapshot_ts": _iso(row.source_snapshot_ts),
        "provenance": provenance_to_dict(row.provenance),
    }


def _metric(raw: float | None, value: float | None, unit: str) -> dict[str, object]:
    return {"raw": raw, "dollar": value, "unit": unit}


def pricing_result_to_dict(row: PricingResult) -> dict[str, object]:
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
            "rt_vega": _metric(row.rt_vega, row.dollar_rt_vega, UNIT_STRINGS["dollar_rt_vega"]),
            "theta": _metric(row.theta, row.dollar_theta, UNIT_STRINGS["dollar_theta_365"]),
            "rho": _metric(row.rho, row.dollar_rho, UNIT_STRINGS["dollar_rho"]),
            "vanna": _metric(row.vanna, row.dollar_vanna, UNIT_STRINGS["dollar_vanna"]),
            "volga": _metric(row.volga, row.dollar_volga, UNIT_STRINGS["dollar_volga"]),
            "charm": _metric(row.charm, row.dollar_charm, UNIT_STRINGS["dollar_charm_365"]),
        },
        "source_snapshot_ts": _iso(row.source_snapshot_ts),
        "provenance": provenance_to_dict(row.provenance),
    }


def _analytics_metric(
    raw: float | None, dollar: float | None, unit: str | None
) -> dict[str, object]:
    return {"raw": raw, "dollar": dollar, "unit": unit}


def _nullable_analytics_metric(
    raw: float | None, dollar: float | None, unit: str | None
) -> dict[str, object]:
    return {"raw": raw, "dollar": dollar, "unit": unit}


def projected_option_analytics_to_dict(row: ProjectedOptionAnalytics) -> dict[str, object]:
    mirror_delta_unit = row.dollar_delta_unit if row.delta_mirror is not None else None
    mirror_theta_unit = row.dollar_theta_unit if row.theta_mirror is not None else None
    mirror_rho_unit = row.dollar_rho_unit if row.rho_mirror is not None else None
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
            "rt_vega": _analytics_metric(
                row.rt_vega, row.dollar_rt_vega, row.dollar_rt_vega_unit
            ),
            "theta": _analytics_metric(row.theta, row.dollar_theta, row.dollar_theta_unit),
            "rho": _analytics_metric(row.rho, row.dollar_rho, row.dollar_rho_unit),
        },
        "price_mirror": row.price_mirror,
        "mirror_metrics": {
            "delta": _nullable_analytics_metric(
                row.delta_mirror, row.dollar_delta_mirror, mirror_delta_unit
            ),
            "theta": _nullable_analytics_metric(
                row.theta_mirror, row.dollar_theta_mirror, mirror_theta_unit
            ),
            "rho": _nullable_analytics_metric(
                row.rho_mirror, row.dollar_rho_mirror, mirror_rho_unit
            ),
        },
        "model_version": row.model_version,
        "pricer_version": row.pricer_version,
        "source_snapshot_ts": _iso(row.source_snapshot_ts),
        "provenance": provenance_to_dict(row.provenance),
    }


def _basket_metric(dollar: float | None, unit: str | None) -> dict[str, object]:
    return {"dollar": dollar, "unit": unit}


def _basket_leg_to_dict(line: LegRisk) -> dict[str, object]:
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


def basket_scenarios_to_dict(result: BasketStressResult) -> dict[str, object]:
    n_cells = len(result.spot_axis) * len(result.vol_axis)
    return {
        "basket_id": result.basket_id,
        "trade_date": result.trade_date.isoformat(),
        "underlying": result.underlying,
        "surface": {
            "spot_shock": list(result.spot_axis),
            "vol_shock": list(result.vol_axis),
            "scenario_pnl": [list(row) for row in result.pnl_grid],
            "scenario_version": result.scenario_version,
            "unit": SCENARIO_PNL_UNIT,
            "n_cells": n_cells,
            "has_holes": False,
            "n_holes": 0,
        },
        "worst_case": {
            "spot_shock": result.worst_spot_shock,
            "vol_shock": result.worst_vol_shock,
            "pnl": result.worst_pnl,
            "unit": SCENARIO_PNL_UNIT,
        },
        "n_legs": result.n_legs,
        "n_resolved": result.n_resolved,
        "gaps": [
            {
                "underlying": gap.underlying,
                "tenor_label": gap.tenor_label,
                "delta_band": gap.delta_band,
                "reason": gap.reason,
            }
            for gap in result.gaps
        ],
        "n_gaps": len(result.gaps),
    }


def slice_plot_series_to_dict(series: SlicePlotSeries) -> dict[str, object]:
    return {
        "raw_k": list(series.raw_k),
        "raw_w": list(series.raw_w),
        "grid_k": list(series.grid_k),
        "fitted_w": list(series.fitted_w),
    }


def dashboard_status_to_dict(status: DashboardStatus) -> dict[str, object]:
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


def _price_spec_to_dict(spec: object) -> dict[str, object]:
    if isinstance(spec, Limit):
        return {"kind": "limit", "price": spec.price}
    return {"kind": "market"}


def ticket_to_dict(ticket: OrderTicket) -> dict[str, object]:
    return {
        "source_basket_id": ticket.source_basket_id,
        "trade_date": ticket.trade_date.isoformat(),
        "underlying": ticket.underlying,
        "target_broker": ticket.target_broker.value,
        "time_in_force": ticket.time_in_force.value,
        "mode": ticket.mode,
        "legs": [
            {
                "instrument_kind": leg.instrument_kind,
                "underlying": leg.underlying,
                "side": leg.side.value,
                "quantity": leg.quantity,
                "price_spec": _price_spec_to_dict(leg.price_spec),
                "tenor_label": leg.tenor_label,
                "delta_band": leg.delta_band,
            }
            for leg in ticket.legs
        ],
        "n_legs": len(ticket.legs),
        "gated": {
            "transmit": False,
            "reason": "3B — sign-and-send is behind an explicit owner gate",
        },
    }


_POSITION_GREEK_UNITS = {
    "delta": UNIT_STRINGS["dollar_delta"],
    "gamma": UNIT_STRINGS["dollar_gamma_one_pct"],
    "vega": UNIT_STRINGS["dollar_vega"],
    "theta": UNIT_STRINGS["dollar_theta_365"],
    "rho": UNIT_STRINGS["dollar_rho"],
}


def _greek_component_to_dict(name: str, component: GreekComponent) -> dict[str, object]:
    return {
        "raw": component.raw,
        "position": component.position,
        "dollar": component.dollar,
        "unit": _POSITION_GREEK_UNITS[name],
    }


def _position_line_to_dict(line: PositionLine) -> dict[str, object]:
    return {
        "contract_key": line.contract_key,
        "underlying": line.underlying,
        "strike": line.strike,
        "expiry": line.expiry,
        "option_right": line.option_right,
        "multiplier": line.multiplier,
        "quantity": line.quantity,
        "broker_contract_id": line.broker_contract_id,
        "mark_price": line.mark_price,
        "market_value": line.market_value,
        "greeks": {
            name: _greek_component_to_dict(name, line.greeks[name])
            for name in ("delta", "gamma", "vega", "theta", "rho")
        },
    }


def position_book_to_dict(book: PositionBook) -> dict[str, object]:
    return {
        "source": book.source,
        "source_ts": _iso(book.source_ts),
        "n_lines": len(book.lines),
        "lines": [_position_line_to_dict(line) for line in book.lines],
        "book": {
            "delta": {"dollar": book.book.delta, "unit": _POSITION_GREEK_UNITS["delta"]},
            "gamma": {"dollar": book.book.gamma, "unit": _POSITION_GREEK_UNITS["gamma"]},
            "vega": {"dollar": book.book.vega, "unit": _POSITION_GREEK_UNITS["vega"]},
            "theta": {"dollar": book.book.theta, "unit": _POSITION_GREEK_UNITS["theta"]},
            "rho": {"dollar": book.book.rho, "unit": _POSITION_GREEK_UNITS["rho"]},
            "market_value": book.book.market_value,
        },
        "priced_contract_keys": book.priced_contract_keys,
        "unpriced_contract_keys": list(book.unpriced_contract_keys),
    }
