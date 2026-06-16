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
    run_id: str | None = None,
) -> list[Any]:
    # ``run_id`` pins the read to one fetch's ``run=`` partition for run-partitioned tables; with
    # ``run_id=None`` the store resolves the newest fetch (and legacy flat data passes through),
    # so callers without a selected fetch keep the prior behaviour.
    rows = store.read(
        table,
        trade_date=trade_date,
        underlying=underlying,
        provider=provider,
        run_id=run_id,
    )
    return [row for row in rows if row.underlying == underlying]
