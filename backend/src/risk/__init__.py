"""The risk engine — portfolio Greeks, monetized sensitivities, and scenario stress.

Pure functions over A's contracts, built on C's frozen pricing interface. The flow:

    from risk import ContractValuationInput, position_risk, aggregate_lines, scenario_grid

1. Resolve one :class:`ContractValuationInput` per held contract (the market state).
2. :func:`position_risk` prices each position into a :class:`PositionRisk` line
   (per-unit Greeks plus monetized dollar sensitivities).
3. :func:`aggregate_lines` nets the lines into :class:`NetSensitivities` by
   instrument / maturity / underlying, projected to A's ``RiskAggregate``.
4. :func:`scenario_grid` + :func:`scenario_line_pnls` stress every position by full
   reprice (the source of truth), with :func:`local_approx_pnl` as the fast path,
   projected to A's ``ScenarioResult``.

Determinism and provenance are the invariants: every emission adapter takes an
injected stamp and never reads a clock, so a risk row reproduces byte-for-byte in
replay. Finite-difference bump sizes live in one versioned place
(:data:`DEFAULT_BUMPS`), shared by the Greeks cross-check and the scenario engine.
"""

from __future__ import annotations

from .aggregate import (
    GROUP_DIMENSIONS,
    AggregationError,
    NetSensitivities,
    aggregate_by_desk,
    aggregate_lines,
    group_key_for,
    risk_aggregate,
)
from .bumps import BUMP_VERSION, DEFAULT_BUMPS, BumpSpec
from .greeks import (
    LotConsistencyError,
    PositionRisk,
    central_difference_greeks,
    net_lots,
    position_risk,
)
from .reconciliation import (
    DEFAULT_RECON_TOLERANCE,
    RECON_TOLERANCE_VERSION,
    BrokerGreeks,
    GreekDiscrepancy,
    ReconciliationTolerance,
    reconcile,
)
from .scenario import (
    GRID_CONSTRUCTION_VERSION,
    ROLL_DOWN_DAYS,
    Scenario,
    ScenarioGridError,
    ScenarioLinePnl,
    WorstCase,
    effective_scenario_version,
    full_reprice_pnl,
    local_approx_pnl,
    local_approx_pnl_fd,
    scenario_grid,
    scenario_line_pnls,
    scenario_result,
    scenario_totals,
    shock_valuation,
    worst_case,
)
from .valuation import (
    CONFIDENCE_LABELS,
    CONFIDENCE_LOW,
    CONFIDENCE_OK,
    ContractValuationInput,
    ValuationError,
    pricing_state_for,
)

# Bump only on a real change to a risk or scenario formula, mirroring PRICER_VERSION.
# Used as the ``code_version`` on stamps the caller builds for D's outputs.
RISK_ENGINE_VERSION = "risk-1.0.0"

__all__ = [
    "BUMP_VERSION",
    "CONFIDENCE_LABELS",
    "CONFIDENCE_LOW",
    "CONFIDENCE_OK",
    "DEFAULT_BUMPS",
    "DEFAULT_RECON_TOLERANCE",
    "GRID_CONSTRUCTION_VERSION",
    "GROUP_DIMENSIONS",
    "RECON_TOLERANCE_VERSION",
    "RISK_ENGINE_VERSION",
    "ROLL_DOWN_DAYS",
    "AggregationError",
    "BrokerGreeks",
    "BumpSpec",
    "ContractValuationInput",
    "GreekDiscrepancy",
    "LotConsistencyError",
    "NetSensitivities",
    "PositionRisk",
    "ReconciliationTolerance",
    "Scenario",
    "ScenarioGridError",
    "ScenarioLinePnl",
    "ValuationError",
    "WorstCase",
    "aggregate_by_desk",
    "aggregate_lines",
    "central_difference_greeks",
    "effective_scenario_version",
    "full_reprice_pnl",
    "group_key_for",
    "local_approx_pnl",
    "local_approx_pnl_fd",
    "net_lots",
    "position_risk",
    "pricing_state_for",
    "reconcile",
    "risk_aggregate",
    "scenario_grid",
    "scenario_line_pnls",
    "scenario_result",
    "scenario_totals",
    "shock_valuation",
    "worst_case",
]
