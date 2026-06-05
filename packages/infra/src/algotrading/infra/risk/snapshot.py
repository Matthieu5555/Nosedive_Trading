"""The canonical risk snapshot: line-level and aggregate risk with its provenance.

The single object the rest of the system (scenarios, dashboards, the risk API) consumes. It
bundles the per-line breakdown, the published aggregates grouped by each configured key, an
optional reconciliation report, and the provenance that makes it reproducible. The blueprint
mandates exactly this versioning (``risk/aggregation.py``: "Version the risk snapshot with
analytics version and position source timestamp"; "Preserve line-level outputs for audit").

The snapshot is a pure function of its inputs — positions, the resolved valuations, the
analytics version, and the position source timestamp — so a stored result regenerates from a
named, dated book. No clock is read here; the position source timestamp and analytics version
are the time anchors, and ``code_version`` records the code that produced it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from algotrading.core.provenance import code_version

from .aggregation import NetSensitivities, aggregate_by_key
from .config import RiskParams
from .greeks import PositionRisk, position_risk
from .positions import PositionSet
from .reconciliation import BrokerGreeks, ReconciliationReport, reconcile_report
from .valuation import ContractValuationInput

_DISTRIBUTION = "algotrading-infra"


class MissingValuationError(KeyError):
    """A position had no resolved valuation — risk cannot be computed for that line.

    A line that cannot be priced is a gap in the risk picture, never silently dropped.
    """


@dataclass(frozen=True, slots=True)
class GroupedRisk:
    """Net aggregates for one grouping key (e.g. ``underlying``), in sorted group order."""

    key: str
    groups: tuple[NetSensitivities, ...]


@dataclass(frozen=True, slots=True)
class RiskSnapshot:
    """Line-level and aggregate risk plus provenance — reproducible from a dated book."""

    lines: tuple[PositionRisk, ...]
    aggregations: tuple[GroupedRisk, ...]
    reconciliation: ReconciliationReport | None
    position_source: str
    position_source_ts: datetime
    analytics_version: str
    config_version: str
    code_version: str

    def grouped(self, key: str) -> tuple[NetSensitivities, ...]:
        """Aggregates published under grouping ``key``."""
        for grouped in self.aggregations:
            if grouped.key == key:
                return grouped.groups
        raise KeyError(key)


def build_risk_snapshot(
    positions: PositionSet,
    valuations: Mapping[str, ContractValuationInput],
    params: RiskParams,
    *,
    analytics_version: str,
    portfolio_id: str,
    broker_greeks: Mapping[str, BrokerGreeks] | None = None,
    desk_of: Mapping[str, str] | None = None,
    steps: int | None = None,
) -> RiskSnapshot:
    """Build the canonical risk snapshot: lines, the configured aggregates, optional
    reconciliation, and provenance. Deterministic in its inputs.

    Each position is joined to its resolved valuation and priced into a
    :class:`PositionRisk` line; a position with no valuation raises
    :class:`MissingValuationError` (named), never a silent drop. Aggregates are produced
    for every key in ``params.grouping_keys`` via the config-driven
    :func:`aggregation.aggregate_by_key`; the ``desk`` key needs ``desk_of``.
    """
    lines: list[PositionRisk] = []
    for pos in positions.positions:
        valuation = valuations.get(pos.contract_key)
        if valuation is None:
            raise MissingValuationError(
                f"no valuation for position contract_key={pos.contract_key!r}"
            )
        lines.append(
            position_risk(
                portfolio_id=portfolio_id,
                quantity=float(pos.quantity),
                valuation=valuation,
                steps=steps,
            )
        )
    line_tuple = tuple(lines)
    aggregations = tuple(
        GroupedRisk(
            key=key,
            groups=tuple(
                aggregate_by_key(line_tuple, portfolio_id=portfolio_id, key=key, desk_of=desk_of)
            ),
        )
        for key in params.grouping_keys
    )
    reconciliation = (
        reconcile_report(
            line_tuple, broker_greeks, tolerance=params.reconciliation_tolerance
        )
        if broker_greeks is not None
        else None
    )
    return RiskSnapshot(
        lines=line_tuple,
        aggregations=aggregations,
        reconciliation=reconciliation,
        position_source=positions.source,
        position_source_ts=positions.source_ts,
        analytics_version=analytics_version,
        config_version=params.config_version,
        code_version=code_version(_DISTRIBUTION),
    )
