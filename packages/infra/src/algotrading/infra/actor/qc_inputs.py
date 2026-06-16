from __future__ import annotations

from dataclasses import dataclass, field

from algotrading.infra.forwards import ForwardEstimate, ParityLine
from algotrading.infra.iv import IvResult
from algotrading.infra.risk import PositionRisk, Scenario
from algotrading.infra.snapshots import SnapshotBatch
from algotrading.infra.surfaces import CalendarViolation, SliceFit


@dataclass(frozen=True, slots=True)
class QcInputs:

    batch: SnapshotBatch | None = None
    underlying_keys: tuple[str, ...] = field(default_factory=tuple)
    expected_chain_keys: tuple[tuple[str, tuple[str, ...]], ...] = field(default_factory=tuple)
    forward_estimates: tuple[ForwardEstimate, ...] = field(default_factory=tuple)
    parity_lines: tuple[tuple[str, float, ParityLine], ...] = field(default_factory=tuple)
    iv_results: tuple[tuple[str, tuple[IvResult, ...]], ...] = field(default_factory=tuple)
    slice_fits: tuple[SliceFit, ...] = field(default_factory=tuple)
    calendar_violations: tuple[tuple[str, tuple[CalendarViolation, ...]], ...] = field(
        default_factory=tuple
    )
    risk_lines: tuple[PositionRisk, ...] = field(default_factory=tuple)
    scenario_grid: tuple[Scenario, ...] = field(default_factory=tuple)
    portfolio_id: str = ""
