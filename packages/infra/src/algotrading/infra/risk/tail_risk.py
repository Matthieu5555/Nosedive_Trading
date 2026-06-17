from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from .scenarios import ScenarioLinePnl, scenario_totals

TAIL_RISK_VERSION = "tail-risk-1.0.0"

DEFAULT_CONFIDENCE_LEVELS = (0.95, 0.99)


class TailRiskError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class TailRiskMetric:

    confidence: float
    var: float
    expected_shortfall: float
    breach_count: int
    sample_size: int


@dataclass(frozen=True, slots=True)
class TailRiskReport:

    tail_risk_version: str
    sample_size: int
    worst_loss: float
    metrics: tuple[TailRiskMetric, ...]


_RANK_SNAP_TOLERANCE = 1e-9


def _validate_confidence(confidence: float) -> None:
    if not 0.0 < confidence < 1.0:
        raise TailRiskError(
            f"confidence must be strictly between 0 and 1, got {confidence}"
        )


def _losses_sorted_descending(pnls: Sequence[float]) -> list[float]:
    return sorted((-pnl for pnl in pnls), reverse=True)


def _tail_count(sample_size: int, confidence: float) -> int:
    raw = (1.0 - confidence) * sample_size
    snapped = round(raw)
    rank = snapped if abs(raw - snapped) <= _RANK_SNAP_TOLERANCE else math.ceil(raw)
    return max(rank, 1)


def value_at_risk(pnls: Sequence[float], confidence: float) -> float:
    _validate_confidence(confidence)
    if not pnls:
        raise TailRiskError("value_at_risk requires at least one P&L observation")
    losses = _losses_sorted_descending(pnls)
    return losses[_tail_count(len(losses), confidence) - 1]


def expected_shortfall(pnls: Sequence[float], confidence: float) -> float:
    _validate_confidence(confidence)
    if not pnls:
        raise TailRiskError("expected_shortfall requires at least one P&L observation")
    losses = _losses_sorted_descending(pnls)
    count = _tail_count(len(losses), confidence)
    return math.fsum(losses[:count]) / count


def tail_risk_metric(pnls: Sequence[float], confidence: float) -> TailRiskMetric:
    _validate_confidence(confidence)
    if not pnls:
        raise TailRiskError("tail_risk_metric requires at least one P&L observation")
    losses = _losses_sorted_descending(pnls)
    count = _tail_count(len(losses), confidence)
    var = losses[count - 1]
    return TailRiskMetric(
        confidence=confidence,
        var=var,
        expected_shortfall=math.fsum(losses[:count]) / count,
        breach_count=count,
        sample_size=len(losses),
    )


def tail_risk_report(
    pnls: Sequence[float],
    *,
    confidence_levels: Sequence[float] = DEFAULT_CONFIDENCE_LEVELS,
) -> TailRiskReport:
    if not pnls:
        raise TailRiskError("tail_risk_report requires at least one P&L observation")
    if not confidence_levels:
        raise TailRiskError("tail_risk_report requires at least one confidence level")
    metrics = tuple(
        tail_risk_metric(pnls, confidence)
        for confidence in sorted(set(confidence_levels))
    )
    return TailRiskReport(
        tail_risk_version=TAIL_RISK_VERSION,
        sample_size=len(pnls),
        worst_loss=max(_losses_sorted_descending(pnls)),
        metrics=metrics,
    )


def scenario_pnl_distribution(cells: Iterable[ScenarioLinePnl]) -> tuple[float, ...]:
    totals = scenario_totals(cells)
    if not totals:
        raise TailRiskError("scenario_pnl_distribution requires at least one scenario cell")
    return tuple(totals[sid] for sid in sorted(totals))


def tail_risk_from_cells(
    cells: Iterable[ScenarioLinePnl],
    *,
    confidence_levels: Sequence[float] = DEFAULT_CONFIDENCE_LEVELS,
) -> TailRiskReport:
    distribution = scenario_pnl_distribution(cells)
    return tail_risk_report(distribution, confidence_levels=confidence_levels)
