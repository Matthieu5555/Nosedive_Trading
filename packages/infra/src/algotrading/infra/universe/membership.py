from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import duckdb
from algotrading.infra.contracts import IndexConstituent
from algotrading.infra.storage import ParquetStore
from algotrading.infra.storage.partitioning import table_dir

from .errors import MembershipError, MembershipRankingError

_TABLE = "index_constituents"

_WEIGHT_SUM_TOLERANCE = 0.02


@dataclass(frozen=True, slots=True)
class MembershipChange:

    index: str
    constituent: str
    effective_add_date: date
    effective_remove_date: date | None
    knowledge_date: date
    vendor: str
    weight: float | None = None


@dataclass(frozen=True, slots=True)
class BasketMember:

    constituent: str
    weight: float | None


def _validate_change(change: MembershipChange) -> None:
    if not change.index or not change.index.strip():
        raise MembershipError(change.index, "index", change.index, "must be a non-empty symbol")
    if not change.constituent or not change.constituent.strip():
        raise MembershipError(
            change.index, "constituent", change.constituent, "must be a non-empty symbol"
        )
    if not change.vendor or not change.vendor.strip():
        raise MembershipError(
            change.index, "vendor", change.vendor, "must name a non-empty data source"
        )
    if change.weight is not None and change.weight < 0:
        raise MembershipError(
            change.index, "weight", change.weight, "weight must be non-negative (None if unknown)"
        )
    if (
        change.effective_remove_date is not None
        and change.effective_remove_date < change.effective_add_date
    ):
        raise MembershipError(
            change.index,
            "effective_remove_date",
            change.effective_remove_date,
            f"must be >= effective_add_date ({change.effective_add_date.isoformat()})",
        )


def _check_snapshot_weight_sums(changes: Sequence[MembershipChange]) -> None:
    groups: dict[tuple[str, date], list[float | None]] = {}
    for change in changes:
        groups.setdefault((change.index, change.knowledge_date), []).append(change.weight)
    for (index, _knowledge), weights in groups.items():
        if any(weight is None for weight in weights):
            raise MembershipError(
                index,
                "weight",
                None,
                "a complete snapshot cannot have a labeled-unavailable (None) weight",
            )
        total = sum(weight for weight in weights if weight is not None)
        if abs(total - 1.0) > _WEIGHT_SUM_TOLERANCE:
            raise MembershipError(
                index,
                "weight",
                total,
                f"a complete-weight snapshot must sum near 1.0 "
                f"(got {total:.4f}, tolerance {_WEIGHT_SUM_TOLERANCE})",
            )


def _to_contract(change: MembershipChange) -> IndexConstituent:
    return IndexConstituent(
        index=change.index,
        constituent=change.constituent,
        effective_add_date=change.effective_add_date,
        effective_remove_date=change.effective_remove_date,
        knowledge_date=change.knowledge_date,
        vendor=change.vendor,
        weight=change.weight,
    )


def ingest_membership_changes(
    store: ParquetStore,
    changes: Sequence[MembershipChange],
    *,
    complete_snapshot: bool = False,
) -> tuple[IndexConstituent, ...]:
    if not changes:
        return ()
    for change in changes:
        _validate_change(change)
    if complete_snapshot:
        _check_snapshot_weight_sums(changes)
    records = tuple(
        sorted(
            (_to_contract(change) for change in changes),
            key=lambda r: (r.index, r.constituent, r.effective_add_date, r.knowledge_date),
        )
    )
    existing = {
        (r.index, r.constituent, r.effective_add_date, r.knowledge_date): r
        for r in store.read(_TABLE)
    }
    fresh: list[IndexConstituent] = []
    for record in records:
        key = (record.index, record.constituent, record.effective_add_date, record.knowledge_date)
        incumbent = existing.get(key)
        if incumbent is None:
            fresh.append(record)
        elif incumbent != record:
            raise MembershipError(
                record.index,
                "knowledge_date",
                record.knowledge_date,
                "a different membership payload already exists for this bitemporal key; "
                "a restatement must use a new knowledge_date, never overwrite history",
            )
    if fresh:
        store.write(_TABLE, fresh)
    return records


_RESOLVE_SQL = """
WITH known AS (
    SELECT *
    FROM read_parquet($files, union_by_name=true, hive_partitioning=false)
    WHERE index = $index
      AND ($known_as_of IS NULL OR knowledge_date <= $known_as_of)
),
latest_knowledge AS (
    -- one row per effective interval: the most recent restatement known by K
    SELECT * FROM known
    QUALIFY row_number() OVER (
        PARTITION BY constituent, effective_add_date
        ORDER BY knowledge_date DESC
    ) = 1
),
probe AS (
    -- one probe row per candidate name so the ASOF JOIN resolves each independently
    SELECT DISTINCT constituent, $as_of_date::DATE AS as_of_date FROM latest_knowledge
),
resolved AS (
    SELECT p.constituent, lk.weight, lk.effective_remove_date
    FROM probe p
    ASOF JOIN latest_knowledge lk
      ON p.constituent = lk.constituent
     AND p.as_of_date >= lk.effective_add_date
)
SELECT constituent, weight
FROM resolved
-- half-open interval: a name removed on the probe date is already out
WHERE effective_remove_date IS NULL OR $as_of_date::DATE < effective_remove_date
ORDER BY constituent
"""


def members(
    store: ParquetStore,
    index: str,
    as_of_date: date,
    *,
    known_as_of: date | None = None,
) -> tuple[BasketMember, ...]:
    base = table_dir(store.root, _TABLE)
    if not base.exists():
        return ()
    files = [str(path) for path in sorted(base.glob("**/*.parquet"))]
    if not files:
        return ()
    connection = duckdb.connect()
    try:
        connection.execute("SET TimeZone='UTC'")
        rows = connection.execute(
            _RESOLVE_SQL,
            {
                "files": files,
                "index": index,
                "known_as_of": known_as_of,
                "as_of_date": as_of_date,
            },
        ).fetchall()
    finally:
        connection.close()
    return tuple(BasketMember(constituent=row[0], weight=row[1]) for row in rows)


def top_n_by_weight(
    store: ParquetStore,
    index: str,
    as_of_date: date,
    n: int,
    *,
    known_as_of: date | None = None,
) -> tuple[BasketMember, ...]:
    if n <= 0:
        raise MembershipRankingError(
            index, "n", n, "must be a positive selection size (the top-N count)"
        )
    basket = members(store, index, as_of_date, known_as_of=known_as_of)
    if not basket:
        return ()
    unweighted = tuple(member.constituent for member in basket if member.weight is None)
    if unweighted:
        raise MembershipRankingError(
            index,
            "weight",
            unweighted,
            "cannot rank a basket with labeled-unavailable (None) weights; "
            f"{len(unweighted)} of {len(basket)} names have no weight "
            f"(e.g. {unweighted[0]!r}) — ingest a weighted source before selecting top-N",
        )
    ranked = sorted(basket, key=lambda member: (-(member.weight or 0.0), member.constituent))
    return tuple(ranked[:n])


def basket_weight_sum(basket: Sequence[BasketMember]) -> float | None:
    if any(member.weight is None for member in basket):
        return None
    return sum(member.weight for member in basket if member.weight is not None)
