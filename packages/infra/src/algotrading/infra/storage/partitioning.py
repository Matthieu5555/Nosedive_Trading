from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from algotrading.infra.contracts.registry import spec_for_table

from .errors import StorageError

_TRADE_DATE_TIMESTAMPS = ("snapshot_ts", "valuation_ts", "canonical_ts", "run_ts", "as_of_ts")

_TRADE_DATE_DATES = ("effective_add_date", "as_of_date")

_UNDERLYING_KEY_FIELDS = ("instrument_key", "contract_key")

_UNDERLYING_FALLBACK_FIELDS = ("index",)


def trade_date_of(record: object) -> date:
    explicit = getattr(record, "trade_date", None)
    if isinstance(explicit, date) and not isinstance(explicit, datetime):
        return explicit
    for field in _TRADE_DATE_TIMESTAMPS:
        value = getattr(record, field, None)
        if isinstance(value, datetime):
            return value.astimezone(UTC).date()
    for field in _TRADE_DATE_DATES:
        value = getattr(record, field, None)
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
    raise StorageError(f"cannot derive a trade date for record {record!r}")


def underlying_of(record: object) -> str:
    explicit = getattr(record, "underlying", None)
    if isinstance(explicit, str) and explicit:
        return explicit
    for field in _UNDERLYING_KEY_FIELDS:
        value = getattr(record, field, None)
        if isinstance(value, str) and value:
            return value.split("|", 1)[0]
    for field in _UNDERLYING_FALLBACK_FIELDS:
        value = getattr(record, field, None)
        if isinstance(value, str) and value:
            return value
    return "_all"


def provider_of(record: object) -> str:
    value = getattr(record, "provider", None)
    if isinstance(value, str) and value:
        return value
    raise StorageError(f"cannot derive a provider for provider-partitioned record {record!r}")


def table_dir(root: Path, table: str) -> Path:
    spec = spec_for_table(table)
    return root / spec.layer / table


def _checked_segment(value: str, kind: str) -> str:
    if not value or "/" in value or "\\" in value or "=" in value:
        raise StorageError(f"invalid partition {kind} {value!r}")
    return value


def _checked_version(version: str) -> str:
    return _checked_segment(version, "version")


def partition_dir(
    root: Path,
    table: str,
    trade_date: date,
    underlying: str,
    version: str | None = None,
    provider: str | None = None,
) -> Path:
    base = table_dir(root, table)
    if provider is not None:
        base = base / f"provider={_checked_segment(provider, 'provider')}"
    base = base / f"trade_date={trade_date.isoformat()}" / f"underlying={underlying}"
    if version is None:
        return base
    return base / f"version={_checked_version(version)}"


def partition_file(
    root: Path,
    table: str,
    trade_date: date,
    underlying: str,
    version: str | None = None,
    provider: str | None = None,
) -> Path:
    return partition_dir(root, table, trade_date, underlying, version, provider) / "data.parquet"
