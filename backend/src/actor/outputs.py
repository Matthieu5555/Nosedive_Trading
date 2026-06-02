"""The derived outputs of one actor run — the byte-identical replay handle.

:class:`ActorOutputs` is what :func:`actor.driver.run_analytics` returns: every
derived contract the actor computed for one trade date, in a fixed order, and
nothing else. It is deliberately separate from persistence so the headline
same-code-path replay test can compare two runs *as values* — drive the actor
once from a live event stream and once from the same events replayed off disk,
and assert the two :class:`ActorOutputs` are equal — without going through
storage. Equality is structural because every contract is a frozen dataclass, so
"byte-identical" is a plain ``==`` here and a Parquet-bytes comparison once
persisted.

The tuples are in the order the actor produces them, and within each tuple the
order is a pure function of the input set (the pure functions and ``net_lots``
guarantee that), so two runs over the same events yield identical tuples.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from contracts import (
    ForwardCurvePoint,
    IvPoint,
    MarketStateSnapshot,
    PricingResult,
    RiskAggregate,
    ScenarioResult,
    SurfaceGrid,
    SurfaceParameters,
)


@dataclass(frozen=True, slots=True)
class ActorOutputs:
    """Every derived contract produced by one actor run, grouped by table.

    All fields default to an empty tuple so a run that produces no risk (no
    positions) or no surfaces (too few IV points) is still a well-formed result,
    not a partially-constructed object. The lists carry the *full* set the actor
    chose to persist; the QC verdicts that ride beside snapshots are not here —
    they are the QC plane's :class:`contracts.QcResult` rows, written separately.
    """

    snapshots: tuple[MarketStateSnapshot, ...] = field(default_factory=tuple)
    forwards: tuple[ForwardCurvePoint, ...] = field(default_factory=tuple)
    iv_points: tuple[IvPoint, ...] = field(default_factory=tuple)
    surface_parameters: tuple[SurfaceParameters, ...] = field(default_factory=tuple)
    surface_grid: tuple[SurfaceGrid, ...] = field(default_factory=tuple)
    pricings: tuple[PricingResult, ...] = field(default_factory=tuple)
    risk_aggregates: tuple[RiskAggregate, ...] = field(default_factory=tuple)
    scenarios: tuple[ScenarioResult, ...] = field(default_factory=tuple)

    def is_empty(self) -> bool:
        """True when the run produced no derived records at all."""
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
            )
        )
