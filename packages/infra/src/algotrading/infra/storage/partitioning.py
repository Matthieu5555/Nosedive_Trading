"""Where on disk a record's partition lives.

The roadmap asks for partitioning by data layer, trade date, and underlying. The
on-disk layout mirrors that exactly:

    <root>/<layer>/<table>/trade_date=<YYYY-MM-DD>/underlying=<SYM>/data.parquet

The layer comes from the table registry. The trade date and underlying are read
from the record: most tables carry them directly; the rest derive the trade date
from their primary timestamp and the underlying from the instrument/contract key
(whose first field is the underlying symbol). A record that carries none of these
cannot be placed, and that is an error rather than a silent dump into a catch-all.

An optional fourth segment, ``version=<V>``, sub-partitions a derived analytic by
the run that produced it, so a restated/replayed output written under a newer code
version coexists with the older one instead of overwriting it (step 13, "versioned
partitions"). It is *off by default*: ``version=None`` yields exactly the layout
above, preserving the original path layout and read/write behavior, so the live
recompute path and every partition written before versioning existed are untouched.
Only the restatement path passes an explicit version, landing beside the live
partition; a version-blind read returns the live rows only (see ``adapter.read``).
A version string is a single path segment — it may not be empty or contain a path
separator or ``=``, which would corrupt the Hive-style tree.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from algotrading.infra.contracts.registry import spec_for_table

from .errors import StorageError

# Datetime fields, in priority order, used to derive a trade date when a record
# has no explicit trade_date column. snapshot_ts covers the derived analytics
# tables, valuation_ts the portfolio/risk tables, canonical_ts raw events, run_ts
# the QC results (whose only timestamp is when the check ran), and as_of_ts the
# broker read-side tables (broker_positions / broker_cash_balances — the instant
# the broker account was read; broker_fills carries an explicit trade_date instead).
_TRADE_DATE_TIMESTAMPS = ("snapshot_ts", "valuation_ts", "canonical_ts", "run_ts", "as_of_ts")

# Date fields (not timestamps) used to derive a partition date when a record has no
# explicit trade_date column and no partitioning timestamp. effective_add_date places
# the reference-layer IndexConstituent: bitemporal membership rows partition by the
# effective interval's start, beneath their index (ADR 0034 §4/§5).
_TRADE_DATE_DATES = ("effective_add_date", "as_of_date")

# Key fields whose first "|"-separated component is the underlying symbol.
_UNDERLYING_KEY_FIELDS = ("instrument_key", "contract_key")

# Fields naming the partition's grouping symbol when there is no `underlying` column.
# `index` places the IndexConstituent under its index symbol (the reference-layer
# equivalent of the underlying segment).
_UNDERLYING_FALLBACK_FIELDS = ("index",)


def trade_date_of(record: object) -> date:
    """Return the trade date a record partitions under."""
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
    """Return the underlying symbol a record partitions under (or ``_all``)."""
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
    """Return the source provider a provider-partitioned record lands under.

    Provider-partitioned tables (ADR 0017 / 0034 §4) carry a ``provider`` segment ahead
    of the trade date so two sources of the same ``(underlying, trade_date)`` never mix on
    disk. A provider-partitioned record without a non-empty ``provider`` cannot be placed,
    and that is an error rather than a silent dump into a catch-all.
    """
    value = getattr(record, "provider", None)
    if isinstance(value, str) and value:
        return value
    raise StorageError(f"cannot derive a provider for provider-partitioned record {record!r}")


def table_dir(root: Path, table: str) -> Path:
    """Return the directory holding all partitions of one table."""
    spec = spec_for_table(table)
    return root / spec.layer / table


def _checked_segment(value: str, kind: str) -> str:
    """Return ``value`` if it is a usable single Hive path segment, else raise.

    A partition segment names a directory (``<kind>=<value>``); an empty value or one
    carrying a path separator or ``=`` would corrupt the Hive-style tree, so it is
    refused at construction time rather than producing a misplaced file.
    """
    if not value or "/" in value or "\\" in value or "=" in value:
        raise StorageError(f"invalid partition {kind} {value!r}")
    return value


def _checked_version(version: str) -> str:
    """Return ``version`` if it is a usable single path segment, else raise."""
    return _checked_segment(version, "version")


def partition_dir(
    root: Path,
    table: str,
    trade_date: date,
    underlying: str,
    version: str | None = None,
    provider: str | None = None,
) -> Path:
    """Return the directory for one partition.

    With ``version=None`` this is the ``(table, trade_date, underlying)`` directory —
    the original, unversioned layout. With a version it descends one level into the
    ``version=<V>`` sub-partition, so a restated analytic lands beside, not on top of,
    the existing one.

    ``provider`` is non-``None`` only for provider-partitioned tables (ADR 0017 /
    0034 §4); it prepends a ``provider=<P>`` segment ahead of the trade date —
    ``<table>/provider=<P>/trade_date=<D>/underlying=<SYM>[/version=<V>]`` — so two
    sources of the same ``(underlying, trade_date)`` land in disjoint partitions. Left
    ``None`` the layout is exactly the historical one, so every existing table is
    untouched.
    """
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
    """Return the single Parquet file path for one partition (version/provider optional)."""
    return partition_dir(root, table, trade_date, underlying, version, provider) / "data.parquet"
