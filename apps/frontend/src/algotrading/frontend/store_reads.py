from __future__ import annotations

from datetime import date
from typing import Any

from algotrading.infra.storage import ParquetStore

QC_FAIL_STATUSES = frozenset({"fail", "failing", "failed", "reject", "error"})


def latest_partition_date(
    partitions: list[tuple[date, str]], underlying: str | None = None
) -> date | None:
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
    rows = store.read(
        table, trade_date=trade_date, underlying=underlying, provider=provider
    )
    return [row for row in rows if row.underlying == underlying]
