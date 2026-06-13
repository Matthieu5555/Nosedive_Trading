"""algotrading.infra.risk — portfolio Greeks, monetized sensitivities, aggregation,
scenario stress, and broker reconciliation (roadmap steps 11-12).

Pure functions over a typed valuation input: price each position through the frozen
pricer, net the lines into portfolio sensitivities grouped by any configured key,
reconcile against the broker (diagnostics only), and stress the book under explicit
shocked market states. The full-reprice scenario PnL and the net aggregates project
into the M0-frozen ``RiskAggregate`` / ``ScenarioResult`` contracts; the versioned
``RiskSnapshot`` and ``ScenarioReport`` are the in-memory surfaces the actor, API, and
dashboards consume. Risk never implements a pricing formula — it binds the pricing seam
through :mod:`valuation` only. See ``README.md`` and ADR 0006.
"""

from __future__ import annotations

from .aggregation import (
    DESK_DIMENSION,
    GROUP_DIMENSIONS,
    AggregationError,
    NetSensitivities,
    aggregate_by_desk,
    aggregate_by_key,
    aggregate_lines,
    group_key_for,
    resolve_grouping_key,
    risk_aggregate,
)
from .attribution import (
    BOOK_CONTRACT_KEY,
    LEVEL_BOOK,
    LEVEL_POSITION,
    BookAttribution,
    LineAttribution,
    RealizedAttributionError,
    RealizedBookAttribution,
    RealizedLineAttribution,
    RealizedMove,
    attribute_book,
    attribute_line,
    attribute_realized_book,
    attribute_realized_line,
    book_attribution_result,
    line_attribution_result,
)
from .basket import BasketVarianceResult, basket_variance
from .book import (
    COMPOSITION_VERSION,
    BookLayerInput,
    book_stress_surface,
    build_book_greeks,
)
from .bumps import BUMP_VERSION, DEFAULT_BUMPS, BumpSpec
from .config import DEFAULT_GROUPING_KEYS, AttributionConfig, RiskParams
from .greeks import (
    LotConsistencyError,
    PositionRisk,
    central_difference_greeks,
    net_lots,
    position_risk,
)
from .multileg import (
    BasketGap,
    BasketRisk,
    LegRisk,
    analytics_cell_key,
    basket_risk,
)
from .positions import Position, PositionSet, hypothetical_positions
from .reconciliation import (
    DEFAULT_RECON_TOLERANCE,
    RECON_TOLERANCE_VERSION,
    BrokerGreeks,
    GreekDiscrepancy,
    ReconciliationReport,
    ReconciliationTolerance,
    reconcile,
    reconcile_report,
)
from .scenarios import (
    GRID_CONSTRUCTION_VERSION,
    FamilyAttribution,
    Scenario,
    ScenarioGridError,
    ScenarioLinePnl,
    ScenarioReport,
    TaylorTerms,
    UnderlyingAttribution,
    WorstCase,
    build_scenario_report,
    effective_scenario_version,
    full_reprice_pnl,
    local_approx_pnl,
    local_approx_pnl_fd,
    scenario_grid,
    scenario_line_pnls,
    scenario_result,
    scenario_totals,
    shock_valuation,
    taylor_terms,
    terms_from_move,
    worst_case,
)
from .snapshot import (
    GroupedRisk,
    MissingValuationError,
    RiskSnapshot,
    build_risk_snapshot,
)
from .valuation import (
    CONFIDENCE_LABELS,
    CONFIDENCE_LOW,
    CONFIDENCE_OK,
    ContractValuationInput,
    ValuationError,
    pricing_state_for,
)

# The risk-engine code version, stamped onto emitted contracts via the provenance
# stamp the caller injects. Bump on a change that can alter an emitted number.
RISK_ENGINE_VERSION = "risk-1.0.0"

__all__ = [
    "RISK_ENGINE_VERSION",
    # book composition (2D)
    "BookLayerInput",
    "COMPOSITION_VERSION",
    "book_stress_surface",
    "build_book_greeks",
    # valuation
    "ContractValuationInput",
    "ValuationError",
    "pricing_state_for",
    "CONFIDENCE_OK",
    "CONFIDENCE_LOW",
    "CONFIDENCE_LABELS",
    # bumps
    "BumpSpec",
    "DEFAULT_BUMPS",
    "BUMP_VERSION",
    # greeks
    "PositionRisk",
    "position_risk",
    "net_lots",
    "central_difference_greeks",
    "LotConsistencyError",
    # aggregation
    "NetSensitivities",
    "aggregate_lines",
    "aggregate_by_desk",
    "aggregate_by_key",
    "group_key_for",
    "resolve_grouping_key",
    "risk_aggregate",
    "GROUP_DIMENSIONS",
    "DESK_DIMENSION",
    "AggregationError",
    # scenarios
    "Scenario",
    "scenario_grid",
    "effective_scenario_version",
    "shock_valuation",
    "full_reprice_pnl",
    "local_approx_pnl",
    "local_approx_pnl_fd",
    "scenario_line_pnls",
    "scenario_totals",
    "worst_case",
    "WorstCase",
    "ScenarioLinePnl",
    "ScenarioReport",
    "FamilyAttribution",
    "UnderlyingAttribution",
    "build_scenario_report",
    "scenario_result",
    "ScenarioGridError",
    "GRID_CONSTRUCTION_VERSION",
    "TaylorTerms",
    "taylor_terms",
    "terms_from_move",
    # attribution (by-Greek axis, 2C)
    "AttributionConfig",
    "LineAttribution",
    "BookAttribution",
    "attribute_line",
    "attribute_book",
    "line_attribution_result",
    "book_attribution_result",
    "BOOK_CONTRACT_KEY",
    "LEVEL_POSITION",
    "LEVEL_BOOK",
    # realized day-over-day attribution (TARGET §5.2)
    "RealizedMove",
    "RealizedLineAttribution",
    "RealizedBookAttribution",
    "RealizedAttributionError",
    "attribute_realized_line",
    "attribute_realized_book",
    # reconciliation
    "BrokerGreeks",
    "GreekDiscrepancy",
    "ReconciliationTolerance",
    "ReconciliationReport",
    "reconcile",
    "reconcile_report",
    "DEFAULT_RECON_TOLERANCE",
    "RECON_TOLERANCE_VERSION",
    # positions
    "Position",
    "PositionSet",
    "hypothetical_positions",
    # basket (index variance, Eq 23 — NOT the multi-leg basket below)
    "BasketVarianceResult",
    "basket_variance",
    # multi-leg basket (2A) — book-additive summation of analytics dollar Greeks
    "BasketRisk",
    "BasketGap",
    "LegRisk",
    "basket_risk",
    "analytics_cell_key",
    # config
    "RiskParams",
    "DEFAULT_GROUPING_KEYS",
    # snapshot
    "RiskSnapshot",
    "GroupedRisk",
    "build_risk_snapshot",
    "MissingValuationError",
]
