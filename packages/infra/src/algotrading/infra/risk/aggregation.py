from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

from algotrading.core.provenance import ProvenanceStamp
from algotrading.infra.contracts import RiskAggregate

from .greeks import PositionRisk, net_lots

GROUP_DIMENSIONS = ("instrument", "maturity", "underlying")

DESK_DIMENSION = "desk"


class AggregationError(Exception):

    def __init__(self, dimension: str) -> None:
        self.dimension = dimension
        super().__init__(
            f"unknown grouping dimension {dimension!r}; expected one of "
            f"{(*GROUP_DIMENSIONS, DESK_DIMENSION)}"
        )


@dataclass(frozen=True, slots=True)
class NetSensitivities:

    portfolio_id: str
    group_key: str
    net_delta: float
    net_gamma: float
    net_vega: float
    net_theta: float
    lines: tuple[PositionRisk, ...]


def group_key_for(line: PositionRisk, dimension: str) -> str:
    if dimension == "instrument":
        return f"instrument:{line.contract_key}"
    if dimension == "maturity":
        return f"maturity:{line.valuation.maturity_years:g}"
    if dimension == "underlying":
        return f"underlying:{line.underlying}"
    raise AggregationError(dimension)


def _by_contract(lines: list[PositionRisk]) -> tuple[PositionRisk, ...]:
    return tuple(sorted(lines, key=lambda line: line.contract_key))


def _net_over(
    portfolio_id: str, group_key: str, lines: tuple[PositionRisk, ...]
) -> NetSensitivities:
    return NetSensitivities(
        portfolio_id=portfolio_id,
        group_key=group_key,
        net_delta=math.fsum(line.position_delta for line in lines),
        net_gamma=math.fsum(line.position_gamma for line in lines),
        net_vega=math.fsum(line.position_vega for line in lines),
        net_theta=math.fsum(line.position_theta for line in lines),
        lines=lines,
    )


def aggregate_lines(
    lines: Iterable[PositionRisk], *, portfolio_id: str, dimension: str
) -> list[NetSensitivities]:
    if dimension not in GROUP_DIMENSIONS:
        raise AggregationError(dimension)
    buckets: dict[str, list[PositionRisk]] = {}
    for line in net_lots(lines):
        buckets.setdefault(group_key_for(line, dimension), []).append(line)
    return [
        _net_over(portfolio_id, key, _by_contract(buckets[key]))
        for key in sorted(buckets)
    ]


def aggregate_by_desk(
    lines: Iterable[PositionRisk], *, portfolio_id: str, desk_of: Mapping[str, str]
) -> list[NetSensitivities]:
    buckets: dict[str, list[PositionRisk]] = {}
    for line in net_lots(lines):
        desk = desk_of.get(line.contract_key, "unassigned")
        buckets.setdefault(f"desk:{desk}", []).append(line)
    return [
        _net_over(portfolio_id, key, _by_contract(buckets[key]))
        for key in sorted(buckets)
    ]


def aggregate_by_key(
    lines: Iterable[PositionRisk],
    *,
    portfolio_id: str,
    key: str,
    desk_of: Mapping[str, str] | None = None,
) -> list[NetSensitivities]:
    if key == DESK_DIMENSION:
        if desk_of is None:
            raise AggregationError(
                "desk grouping requires a contract_key -> desk mapping (desk_of)"
            )
        return aggregate_by_desk(lines, portfolio_id=portfolio_id, desk_of=desk_of)
    return aggregate_lines(lines, portfolio_id=portfolio_id, dimension=key)


def resolve_grouping_key(key: str) -> Callable[..., list[NetSensitivities]]:
    known = (*GROUP_DIMENSIONS, DESK_DIMENSION)
    if key not in known:
        raise AggregationError(key)
    if key == DESK_DIMENSION:
        return aggregate_by_desk
    return aggregate_lines


def risk_aggregate(
    net: NetSensitivities,
    *,
    valuation_ts: datetime,
    source_snapshot_ts: datetime,
    provenance: ProvenanceStamp,
) -> RiskAggregate:
    return RiskAggregate(
        valuation_ts=valuation_ts,
        portfolio_id=net.portfolio_id,
        group_key=net.group_key,
        net_delta=net.net_delta,
        net_gamma=net.net_gamma,
        net_vega=net.net_vega,
        net_theta=net.net_theta,
        source_snapshot_ts=source_snapshot_ts,
        provenance=provenance,
    )
