from __future__ import annotations

from dataclasses import dataclass, field

from algotrading.infra.contracts import (
    ForwardCurvePoint,
    IvPoint,
    MarketStateSnapshot,
    PricingResult,
    ProjectedOptionAnalytics,
    RiskAggregate,
    ScenarioResult,
    SurfaceGrid,
    SurfaceParameters,
)


@dataclass(frozen=True, slots=True)
class ActorOutputs:

    snapshots: tuple[MarketStateSnapshot, ...] = field(default_factory=tuple)
    forwards: tuple[ForwardCurvePoint, ...] = field(default_factory=tuple)
    iv_points: tuple[IvPoint, ...] = field(default_factory=tuple)
    surface_parameters: tuple[SurfaceParameters, ...] = field(default_factory=tuple)
    surface_grid: tuple[SurfaceGrid, ...] = field(default_factory=tuple)
    pricings: tuple[PricingResult, ...] = field(default_factory=tuple)
    risk_aggregates: tuple[RiskAggregate, ...] = field(default_factory=tuple)
    scenarios: tuple[ScenarioResult, ...] = field(default_factory=tuple)
    projected_analytics: tuple[ProjectedOptionAnalytics, ...] = field(default_factory=tuple)

    def is_empty(self) -> bool:
        return not any(
            (
                self.snapshots,
                self.forwards,
                self.iv_points,
                self.surface_parameters,
                self.surface_grid,
                self.pricings,
                self.risk_aggregates,
                self.scenarios,
                self.projected_analytics,
            )
        )
