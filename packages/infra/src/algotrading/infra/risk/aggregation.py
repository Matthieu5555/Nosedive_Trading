"""Aggregate per-position risk lines into net sensitivities, by grouping dimension.

Step 11 aggregates the line-level risk "by instrument, maturity, underlying, and any
desk grouping key", and the blueprint's ``risk/aggregation.py`` responsibility is to
"merge positions with analytics results and produce line-level and aggregate
sensitivities ... support grouping by any configured key such as underlying, maturity
bucket, or desk category" while "preserv[ing] line-level outputs for audit". The
headline invariants are that the sum of the lines equals the aggregate and that the
aggregate does not depend on the order positions arrive in (``tasks/TESTING.md``,
``documentation/blueprint/08-acceptance-tests.md`` §risk: "Portfolio aggregates
reconcile to line-level sums"). Both fall out of summing a deterministic, order-free
reduction over signed, multiplier-scaled per-position sensitivities.

Net sensitivities are the contract-level (``per_unit * multiplier * quantity``) Greeks
— share/contract-equivalent — so contracts with different multipliers sum coherently.
Dollar monetization stays at the line (it is currency-tagged and not summed across
currencies); the aggregate carries the raw net sensitivities the frozen
:class:`algotrading.infra.contracts.RiskAggregate` contract defines, and the stamp is
injected at the emission boundary, never read from a clock here.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

from algotrading.core.provenance import ProvenanceStamp
from algotrading.infra.contracts import RiskAggregate

from .greeks import PositionRisk, net_lots

# The grouping dimensions step 11 names. A desk key is supplied by the caller as an
# explicit mapping (a desk is an operational grouping risk does not define), handled by
# ``aggregate_by_desk``; these three are intrinsic to the line.
GROUP_DIMENSIONS = ("instrument", "maturity", "underlying")

# The desk dimension is config-addressable too, but resolves through a caller-supplied
# ``contract_key -> desk`` mapping rather than an intrinsic field on the line.
DESK_DIMENSION = "desk"


class AggregationError(Exception):
    """An aggregation was asked for an unknown grouping dimension."""

    def __init__(self, dimension: str) -> None:
        self.dimension = dimension
        super().__init__(
            f"unknown grouping dimension {dimension!r}; expected one of "
            f"{(*GROUP_DIMENSIONS, DESK_DIMENSION)}"
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
    Same-contract lots are netted first (:func:`net_lots`), so duplicate lots of one
    contract collapse to a single canonical line rather than ordering by arrival.
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
    lines: Iterable[PositionRisk], *, portfolio_id: str, desk_of: Mapping[str, str]
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


def aggregate_by_key(
    lines: Iterable[PositionRisk],
    *,
    portfolio_id: str,
    key: str,
    desk_of: Mapping[str, str] | None = None,
) -> list[NetSensitivities]:
    """Config-driven dispatch: aggregate by a named grouping key.

    ``instrument``/``maturity``/``underlying`` resolve to the intrinsic
    :func:`aggregate_lines`; ``desk`` resolves to :func:`aggregate_by_desk` and
    requires ``desk_of``. An unknown key is an error (no silent fallback) — this is
    the seam the configured ``grouping_keys`` in :mod:`config` drive, so a typo in
    config fails loudly before any snapshot is published.
    """
    if key == DESK_DIMENSION:
        if desk_of is None:
            raise AggregationError(
                "desk grouping requires a contract_key -> desk mapping (desk_of)"
            )
        return aggregate_by_desk(lines, portfolio_id=portfolio_id, desk_of=desk_of)
    return aggregate_lines(lines, portfolio_id=portfolio_id, dimension=key)


def resolve_grouping_key(key: str) -> Callable[..., list[NetSensitivities]]:
    """Resolve a configured grouping-key name to its aggregator, or fail loudly.

    Used to validate ``config.grouping_keys`` at load time so an unknown key is caught
    before any snapshot is built, not at aggregation time.
    """
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
    """Project net sensitivities into the frozen ``RiskAggregate`` contract.

    The provenance stamp is built by the caller (with an injected ``calc_ts``) and
    passed in, so this stays a pure function of its inputs with no wall-clock read —
    the discipline that makes a risk row reproduce byte-for-byte in replay.
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
