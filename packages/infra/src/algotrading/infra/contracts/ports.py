from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class StorageRepository(Protocol):

    def write(
        self, table: str, records: Sequence[object], *, version: str | None = None
    ) -> None:
        ...

    def read(
        self,
        table: str,
        *,
        trade_date: date | None = None,
        underlying: str | None = None,
        version: str | None = None,
        provider: str | None = None,
    ) -> list[Any]:
        ...

    def list_partitions(self, table: str) -> list[tuple[date, str]]:
        ...

    def list_versions(
        self, table: str, trade_date: date, underlying: str, provider: str | None = None
    ) -> list[str]:
        ...

    def delete_partition(
        self,
        table: str,
        trade_date: date,
        underlying: str,
        version: str | None = None,
        provider: str | None = None,
    ) -> None:
        ...

    def source_records_for(self, record: object) -> dict[str, list[Any]]:
        ...

    def raw_events_for(self, derived_record: object) -> list[Any]:
        ...
