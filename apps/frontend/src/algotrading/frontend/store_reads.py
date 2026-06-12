"""Shared read-back idioms over the persisted store (BFF-side, read-only).

Two store-read patterns recurred across the routers with near-identical comments —
"the latest partition date for an underlying" and "a version-blind read narrowed to
one underlying" — plus the QC fail-status vocabulary the health and coverage routers
each kept privately. One home for all three. The eventual filter pushdown belongs in
the store API itself (REP2's territory); when it lands these shrink to one-liners.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from algotrading.infra.storage import ParquetStore

# QC result statuses that mean a check failed (lower-cased before comparison).
QC_FAIL_STATUSES = frozenset({"fail", "failing", "failed", "reject", "error"})


def latest_partition_date(
    partitions: list[tuple[date, str]], underlying: str | None = None
) -> date | None:
    """The most recent partition date (optionally for one underlying), or ``None``."""
    return max(
        (
            part_date
            for part_date, part_underlying in partitions
            if underlying is None or part_underlying == underlying
        ),
        default=None,
    )


def read_for_underlying(
    store: ParquetStore,
    table: str,
    underlying: str,
    *,
    trade_date: date | None = None,
    provider: str | None = None,
) -> list[Any]:
    """A version-blind read narrowed to one underlying's rows.

    The store narrows to the underlying's partitions, and the row filter on top
    guarantees a per-underlying query never bleeds in another name's rows — the belt
    each router used to spell inline beside the same comment. ``trade_date=None``
    reads across every persisted day; ``provider=None`` across every provider.
    """
    rows = store.read(
        table, trade_date=trade_date, underlying=underlying, provider=provider
    )
    return [row for row in rows if row.underlying == underlying]
