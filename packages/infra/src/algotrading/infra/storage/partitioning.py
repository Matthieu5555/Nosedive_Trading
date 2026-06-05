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
# tables, valuation_ts the portfolio/risk tables, canonical_ts raw events, and
# run_ts the QC results (whose only timestamp is when the check ran).
_TRADE_DATE_TIMESTAMPS = ("snapshot_ts", "valuation_ts", "canonical_ts", "run_ts")

# Key fields whose first "|"-separated component is the underlying symbol.
_UNDERLYING_KEY_FIELDS = ("instrument_key", "contract_key")


def trade_date_of(record: object) -> date:
    """Return the trade date a record partitions under."""
    explicit = getattr(record, "trade_date", None)
    if isinstance(explicit, date) and not isinstance(explicit, datetime):
        return explicit
    for field in _TRADE_DATE_TIMESTAMPS:
        value = getattr(record, field, None)
        if isinstance(value, datetime):
            return value.astimezone(UTC).date()
    as_of = getattr(record, "as_of_date", None)
    if isinstance(as_of, date):
        return as_of
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
    return "_all"


def table_dir(root: Path, table: str) -> Path:
    """Return the directory holding all partitions of one table."""
    spec = spec_for_table(table)
    return root / spec.layer / table


def _checked_version(version: str) -> str:
    """Return ``version`` if it is a usable single path segment, else raise.

    A version names a sub-partition directory (``version=<V>``); an empty value or
    one carrying a path separator or ``=`` would corrupt the Hive-style tree, so it
    is refused at construction time rather than producing a misplaced file.
    """
    if not version or "/" in version or "\\" in version or "=" in version:
        raise StorageError(f"invalid partition version {version!r}")
    return version


def partition_dir(
    root: Path,
    table: str,
    trade_date: date,
    underlying: str,
    version: str | None = None,
) -> Path:
    """Return the directory for one partition.

    With ``version=None`` this is the ``(table, trade_date, underlying)`` directory —
    the original, unversioned layout. With a version it descends one level into the
    ``version=<V>`` sub-partition, so a restated analytic lands beside, not on top of,
    the existing one.
    """
    base = (
        table_dir(root, table)
        / f"trade_date={trade_date.isoformat()}"
        / f"underlying={underlying}"
    )
    if version is None:
        return base
    return base / f"version={_checked_version(version)}"


def partition_file(
    root: Path,
    table: str,
    trade_date: date,
    underlying: str,
    version: str | None = None,
) -> Path:
    """Return the single Parquet file path for one partition (version optional)."""
    return partition_dir(root, table, trade_date, underlying, version) / "data.parquet"
