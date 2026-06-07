"""Point-in-time index membership: dated changes in, as-of basket out (WS 1A).

This is the single most look-ahead-sensitive piece in the index pipeline. Index →
constituent membership is stored as **bitemporal reference data** (one
:class:`~algotrading.infra.contracts.IndexConstituent` per dated change), and every
historical basket is reconstructed *as it stood on the date being reconstructed* —
never today's list applied backwards.

Two halves:

* :func:`ingest_membership_changes` — load dated add/remove changes (the OQ-3 source,
  Siblis Research, or a STOXX/EODHD cross-check on the same contract) into the append-only
  ``index_constituents`` Parquet table, validating every change before a byte is written.
  Raw-source *parsing* is kept out of this function (callers hand it already-typed
  :class:`MembershipChange` rows) so a second vendor lands on the same contract.
* :func:`members` — the as-of resolver. ``members(index, as_of_date)`` returns the basket
  exactly as it stood on ``as_of_date``, with that date's weights, through a DuckDB
  ``ASOF JOIN`` over the Parquet store (ADR 0033). This is the gate every historical
  membership join goes through; there is no path that reads "current" membership for a
  past date.

**The as-of contract (read this before calling :func:`members`).** ``as_of_date`` is the
date to *reconstruct the basket as of* — pass the date being analyzed/replayed, **never**
``date.today()`` for a historical computation. The interval convention is **half-open**,
``[effective_add_date, effective_remove_date)``: a name is in the basket on its add date
and out on its remove date. The optional ``known_as_of`` is the *knowledge* axis — "as the
data was known on date K"; left ``None`` it uses every recorded fact (the latest restatement
of each interval). A later vendor restatement does not erase what was known earlier:
``members(index, D, known_as_of=K)`` returns the basket as believed on ``K``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import duckdb
from algotrading.infra.contracts import IndexConstituent
from algotrading.infra.storage import ParquetStore
from algotrading.infra.storage.partitioning import table_dir

from .errors import MembershipError

_TABLE = "index_constituents"

# Basket weights that are all present (no None) should sum near 1.0 where the source gives
# full weights. The tolerance is generous: vendor weights are rounded and a basket can
# legitimately miss a name mid-restatement. A gross miss (sum far from 1) is a corrupt
# source, caught on write; a partial/labeled-unavailable basket (any None weight) skips
# the check entirely rather than being forced to a wrong total.
_WEIGHT_SUM_TOLERANCE = 0.02


@dataclass(frozen=True, slots=True)
class MembershipChange:
    """One typed, dated membership change — the unit the ingester writes.

    This is the already-parsed form: a vendor-specific reader turns a raw CSV/JSON row
    into one of these, so the typed contract and the raw parsing never entangle. ``weight``
    is ``None`` when the source does not provide it (labeled unavailable, never zeroed).
    """

    index: str
    constituent: str
    effective_add_date: date
    effective_remove_date: date | None
    knowledge_date: date
    vendor: str
    weight: float | None = None


@dataclass(frozen=True, slots=True)
class BasketMember:
    """One resolved constituent in an as-of basket: the name and its as-of weight."""

    constituent: str
    weight: float | None


def _validate_change(change: MembershipChange) -> None:
    """Reject a malformed change before it is written — labeled, never coerced."""
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
    """Reject a declared full-snapshot whose complete weights do not sum near 1.0.

    Called only when the caller declares the batch a *complete snapshot* (``complete_snapshot=
    True`` on :func:`ingest_membership_changes`): a loader that knows it is writing the whole
    weighted basket for an index as of one knowledge date. Whether a batch is a complete
    snapshot or an incremental change is the **caller's** knowledge, not something to infer
    from coincidentally-shared dates, so this is opt-in rather than heuristic.

    Per ``(index, knowledge_date)`` group: every name must carry a weight (a complete snapshot
    cannot have a labeled-unavailable weight — that contradicts "complete"), and the weights
    must sum near 1.0. A missing weight or a gross sum miss is rejected with the offending
    index named, never silently zeroed or forced to a total (the economic-correctness bug the
    spec warns about).
    """
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
    """Validate and write a batch of dated membership changes, append-only.

    Every change is validated first (no negative weight; ``remove >= add``; non-empty
    index/constituent/vendor). When ``complete_snapshot=True`` the caller is declaring the
    batch the whole weighted basket for an index as of one knowledge date, and the per-index
    weights must then be complete and sum near 1.0 (:func:`_check_snapshot_weight_sums`); the
    default ``False`` is an incremental change load, where partial/labeled-unavailable weights
    are expected and the sum check does not apply. The batch is then written through the
    storage adapter into the append-only ``index_constituents`` reference table. The write is
    order-independent:
    the on-disk membership and the resolved baskets do not depend on the order changes are
    ingested in (the adapter partitions by index/effective-add-date and the resolver sorts).
    Returns the typed contracts written, sorted canonically, so a caller has a deterministic
    handle on the batch.

    A re-ingest of the *same* change (same full bitemporal key, same payload) is a no-op
    against the append-only layer; an attempt to write a *different* payload under an
    existing key is refused by the adapter's append-only check, which is the immutability
    guarantee (a restatement must use a new ``knowledge_date``, i.e. a new key).
    """
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
    # Skip rows already on disk with identical payload so a re-ingest is idempotent rather
    # than an append-only collision (mirrors materialize_universe's discipline).
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


# The as-of resolver. Two-stage point-in-time semantics over the bitemporal table:
#
#   1. Knowledge axis. Keep only facts known by `known_as_of` (knowledge_date <= K), then
#      for each effective interval (constituent, effective_add_date) keep the row with the
#      LATEST knowledge_date — the most recent restatement believed as of K. This is what
#      makes a later vendor correction not erase what was known earlier.
#   2. Effective axis (the ASOF JOIN). Build a probe row per candidate constituent
#      (constituent, as_of_date) and ASOF JOIN each against that constituent's interval
#      rows on `effective_add_date <= as_of_date`, so the engine picks, per constituent, the
#      single latest interval that had started by the probe date. Then keep only the ones
#      whose half-open interval still contains the date:
#      `effective_remove_date IS NULL OR as_of_date < effective_remove_date`.
#
# Expressed in DuckDB SQL (native ASOF JOIN, ADR 0033) rather than a hand-rolled merge, so
# the point-in-time semantics are the engine's, not ours to get subtly wrong. The ASOF JOIN
# is the resolution itself, not a decoration: it is what reduces each constituent's interval
# history to the one interval in force on the probe date.
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
    """Resolve the index basket exactly as it stood on ``as_of_date`` — the no-look-ahead gate.

    Returns the constituents whose half-open ``[effective_add_date, effective_remove_date)``
    interval contains ``as_of_date``, each with that date's weight, sorted by constituent.
    The resolution runs entirely through a DuckDB ``ASOF JOIN`` over the Parquet store
    (ADR 0033): there is no code path that reads the *latest* membership and applies it to a
    past date.

    ``as_of_date`` is the date to reconstruct as of — pass the date being analyzed or
    replayed, **never** today's date for a historical computation. ``known_as_of`` selects
    the *knowledge* axis: "as the membership was known on date K"; left ``None`` it uses the
    latest restatement of each interval. An unknown index, or a date before the index's
    earliest record, yields an empty basket (a labeled empty result, not a crash).
    """
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


def basket_weight_sum(basket: Sequence[BasketMember]) -> float | None:
    """Sum a basket's weights, or ``None`` if any weight is unavailable.

    Returns ``None`` (not ``0.0``) when the basket has any labeled-unavailable weight, so a
    partial-weight source is never silently treated as a complete one. A caller checking the
    "weights sum near 1.0" invariant must treat ``None`` as "not assertable", never as zero.
    """
    if any(member.weight is None for member in basket):
        return None
    return sum(member.weight for member in basket if member.weight is not None)
