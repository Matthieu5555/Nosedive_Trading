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
    pass


@dataclass(frozen=True, slots=True)
class GroupedRisk:

    key: str
    groups: tuple[NetSensitivities, ...]


@dataclass(frozen=True, slots=True)
class RiskSnapshot:

    lines: tuple[PositionRisk, ...]
    aggregations: tuple[GroupedRisk, ...]
    reconciliation: ReconciliationReport | None
    position_source: str
    position_source_ts: datetime
    analytics_version: str
    config_version: str
    code_version: str

    def grouped(self, key: str) -> tuple[NetSensitivities, ...]:
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
