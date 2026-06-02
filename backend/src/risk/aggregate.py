"""Aggregate per-position risk lines into net sensitivities, by grouping dimension.

Step 11 aggregates the line-level risk "by instrument, maturity, underlying, and
any desk grouping key", and the headline invariants are that the sum of the lines
equals the aggregate and that the aggregate does not depend on the order positions
arrive in (``tasks/TESTING.md``). Both fall out of summing a deterministic,
order-free reduction over signed, multiplier-scaled per-position sensitivities.

Net sensitivities are the contract-level (``per_unit * multiplier * quantity``)
Greeks — share/contract-equivalent — so contracts with different multipliers sum
coherently. Dollar monetization stays at the line (it is currency-tagged and not
summed across currencies); the aggregate carries the raw net sensitivities A's
:class:`RiskAggregate` contract defines, and the stamp is injected at the emission
boundary, never read from a clock here.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from contracts import RiskAggregate
from provenance import ProvenanceStamp

from .greeks import PositionRisk, net_lots

# The grouping dimensions step 11 names. A desk key is supplied by the caller as an
# explicit mapping (a desk is an operational grouping D does not define), handled by
# ``aggregate_by_desk``; these three are intrinsic to the line.
GROUP_DIMENSIONS = ("instrument", "maturity", "underlying")


class AggregationError(Exception):
    """An aggregation was asked for an unknown grouping dimension."""

    def __init__(self, dimension: str) -> None:
        self.dimension = dimension
        super().__init__(
            f"unknown grouping dimension {dimension!r}; expected one of {GROUP_DIMENSIONS}"
        )


@dataclass(frozen=True, slots=True)
class NetSensitivities:
    """The net risk of one portfolio group: summed sensitivities plus its lines.

    Keeps the contributing lines so the aggregate stays explainable — debugging
    starts at the line, and a top-contributor view needs them. Net Greeks are the
    signed sums of the lines' position-level sensitivities.
    """

    portfolio_id: str
    group_key: str
    net_delta: float
    net_gamma: float
    net_vega: float
    net_theta: float
    lines: tuple[PositionRisk, ...]


def group_key_for(line: PositionRisk, dimension: str) -> str:
    """The group key string for a line under a named intrinsic dimension."""
    if dimension == "instrument":
        return f"instrument:{line.contract_key}"
    if dimension == "maturity":
        return f"maturity:{line.valuation.maturity_years:g}"
    if dimension == "underlying":
        return f"underlying:{line.underlying}"
    raise AggregationError(dimension)


def _by_contract(lines: list[PositionRisk]) -> tuple[PositionRisk, ...]:
    """Lines in a fixed order (by contract key), so a group is order-free."""
    return tuple(sorted(lines, key=lambda line: line.contract_key))


def _net_over(
    portfolio_id: str, group_key: str, lines: tuple[PositionRisk, ...]
) -> NetSensitivities:
    return NetSensitivities(
        portfolio_id=portfolio_id,
        group_key=group_key,
        net_delta=sum(line.position_delta for line in lines),
        net_gamma=sum(line.position_gamma for line in lines),
        net_vega=sum(line.position_vega for line in lines),
        net_theta=sum(line.position_theta for line in lines),
        lines=lines,
    )


def aggregate_lines(
    lines: Iterable[PositionRisk], *, portfolio_id: str, dimension: str
) -> list[NetSensitivities]:
    """Group lines by an intrinsic dimension and net each group.

    The result is ordered by ``group_key`` and each group's lines are ordered by
    contract key, so the output is a pure function of the input *set* — shuffling
    the input cannot change it (the reordering-invariance property test binds here).
    Same-contract lots are netted first (:func:`risk.net_lots`), so duplicate lots of
    one contract collapse to a single canonical line rather than ordering by arrival.
    """
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
    lines: Iterable[PositionRisk], *, portfolio_id: str, desk_of: dict[str, str]
) -> list[NetSensitivities]:
    """Group lines by a caller-supplied desk key (contract_key -> desk name).

    A line whose contract is not in ``desk_of`` falls into the ``"desk:unassigned"``
    group rather than being silently dropped — an unmapped position is a visible
    fact, not a hole in the book. Same-contract lots are netted first, so a desk's
    lines are one-per-contract in canonical order.
    """
    buckets: dict[str, list[PositionRisk]] = {}
    for line in net_lots(lines):
        desk = desk_of.get(line.contract_key, "unassigned")
        buckets.setdefault(f"desk:{desk}", []).append(line)
    return [
        _net_over(portfolio_id, key, _by_contract(buckets[key]))
        for key in sorted(buckets)
    ]


def risk_aggregate(
    net: NetSensitivities,
    *,
    valuation_ts: datetime,
    source_snapshot_ts: datetime,
    provenance: ProvenanceStamp,
) -> RiskAggregate:
    """Project net sensitivities into A's ``RiskAggregate`` contract.

    The provenance stamp is built by the caller (with an injected ``calc_ts``) and
    passed in, so this stays a pure function of its inputs with no wall-clock read —
    the same discipline C's emission adapters follow, and what makes a risk row
    reproduce byte-for-byte in replay.
    """
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
