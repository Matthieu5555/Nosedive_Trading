"""DuckDB-over-Parquet storage: validated writes, queryable reads, clean lineage.

One :class:`ParquetStore` owns a data root and reads/writes every table family
through the typed contracts. The design holds three platform invariants:

* **Write-ahead validation.** Every record is validated before a single byte is
  written, so malformed data is rejected at the door with an explicit error.
* **Append-only raw layer.** Writing a row whose primary key already exists in an
  append-only table (raw events, instrument master) is refused. Derived layers
  are recompute-friendly: writing a partition replaces just that partition's
  file, so recomputing one derived slice never rewrites the raw layer.
* **Identical live/replay schema.** Both paths build the Arrow schema from the
  contract, so a partition written live and one written in replay are the same
  shape by construction.

Reads and lineage go through DuckDB querying the Parquet files, which is the
"one query" the roadmap asks for when answering "which raw records produced
this?".
"""

from __future__ import annotations

import shutil
from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from contracts.registry import spec_for_table
from contracts.validation import validate_record

from .errors import AppendOnlyViolation, DuplicateKeyInBatch
from .partitioning import (
    partition_dir,
    partition_file,
    table_dir,
    trade_date_of,
    underlying_of,
)
from .schema import arrow_schema
from .serialization import from_row, to_row


def primary_key_of(table: str, record: object) -> tuple[object, ...]:
    """Return a record's primary-key tuple, in the registry's key order."""
    spec = spec_for_table(table)
    return tuple(getattr(record, name) for name in spec.primary_key)


def _rows_to_arrow(rows: list[dict[str, Any]], schema: pa.Schema) -> pa.Table:
    columns = {name: [row.get(name) for row in rows] for name in schema.names}
    return pa.table(columns, schema=schema)


class ParquetStore:
    """Read/write adapter for all contract tables over a single data root."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    # -- writing ----------------------------------------------------------
    def write(self, table: str, records: Sequence[object]) -> None:
        """Validate and persist a batch of records for one table.

        Append-only tables reject any primary key that already exists on disk;
        other tables replace each touched partition with the records supplied for
        it (recompute semantics). Raises before writing anything if the batch is
        internally inconsistent or any record is malformed.
        """
        if not records:
            return

        spec = spec_for_table(table)
        for record in records:
            validate_record(table, record)

        seen: set[tuple[object, ...]] = set()
        for record in records:
            key = primary_key_of(table, record)
            if key in seen:
                raise DuplicateKeyInBatch(table, key)
            seen.add(key)

        grouped: dict[tuple[date, str], list[object]] = defaultdict(list)
        for record in records:
            grouped[(trade_date_of(record), underlying_of(record))].append(record)

        schema = arrow_schema(spec.contract)
        for (trade_date, underlying), partition_records in grouped.items():
            self._write_partition(
                table, spec.append_only, schema, trade_date, underlying, partition_records
            )

    def _write_partition(
        self,
        table: str,
        append_only: bool,
        schema: pa.Schema,
        trade_date: date,
        underlying: str,
        records: list[object],
    ) -> None:
        spec = spec_for_table(table)
        path = partition_file(self.root, table, trade_date, underlying)
        new_rows = [to_row(spec.contract, record) for record in records]
        new_table = _rows_to_arrow(new_rows, schema)

        if append_only and path.exists():
            # Read only the file's own columns. trade_date and underlying are also
            # stored as real columns inside the file, so pyarrow must NOT re-infer
            # them from the Hive-style directory names: that produces dictionary
            # <string> columns that collide with the file's date32/string ones and
            # breaks the concat below. partitioning=None disables that inference.
            existing = pq.read_table(path, partitioning=None)
            existing_keys = set(
                zip(
                    *(existing.column(name).to_pylist() for name in spec.primary_key),
                    strict=True,
                )
            )
            for record in records:
                key = primary_key_of(table, record)
                if key in existing_keys:
                    raise AppendOnlyViolation(table, key)
            new_table = pa.concat_tables([existing, new_table])

        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(new_table, path)

    # -- reading ----------------------------------------------------------
    def _partition_files(
        self, table: str, trade_date: date | None, underlying: str | None
    ) -> list[Path]:
        if trade_date is not None and underlying is not None:
            single = partition_file(self.root, table, trade_date, underlying)
            return [single] if single.exists() else []
        base = table_dir(self.root, table)
        if not base.exists():
            return []
        return sorted(base.glob("**/*.parquet"))

    def read(
        self,
        table: str,
        *,
        trade_date: date | None = None,
        underlying: str | None = None,
    ) -> list[Any]:
        """Read records for a table (optionally one partition) back into contracts.

        Uses DuckDB over the Parquet files with ``union_by_name`` so partitions
        written under an older, narrower schema remain readable — missing columns
        come back as ``None``.
        """
        spec = spec_for_table(table)
        files = self._partition_files(table, trade_date, underlying)
        if not files:
            return []
        connection = duckdb.connect()
        try:
            connection.execute("SET TimeZone='UTC'")
            relation = connection.execute(
                "SELECT * FROM read_parquet(?, union_by_name=true, hive_partitioning=false)",
                [[str(path) for path in files]],
            )
            column_names = [description[0] for description in relation.description]
            rows = [
                dict(zip(column_names, values, strict=True)) for values in relation.fetchall()
            ]
        finally:
            connection.close()
        return [from_row(spec.contract, row) for row in rows]

    # -- partition management --------------------------------------------
    def list_partitions(self, table: str) -> list[tuple[date, str]]:
        """List the (trade_date, underlying) partitions present for a table."""
        base = table_dir(self.root, table)
        if not base.exists():
            return []
        found: list[tuple[date, str]] = []
        for date_dir in sorted(base.glob("trade_date=*")):
            trade_date = date.fromisoformat(date_dir.name.split("=", 1)[1])
            for underlying_dir in sorted(date_dir.glob("underlying=*")):
                underlying = underlying_dir.name.split("=", 1)[1]
                found.append((trade_date, underlying))
        return found

    def delete_partition(self, table: str, trade_date: date, underlying: str) -> None:
        """Delete one partition. Idempotent: deleting a missing partition is fine."""
        target = partition_dir(self.root, table, trade_date, underlying)
        if target.exists():
            shutil.rmtree(target)

    # -- lineage ----------------------------------------------------------
    def raw_events_for(self, derived_record: object) -> list[Any]:
        """Return the raw events that produced a derived record — in one query.

        Reads the source record ids off the derived record's provenance stamp and
        selects exactly those rows from the raw-event layer.
        """
        provenance = getattr(derived_record, "provenance", None)
        if provenance is None:
            return []
        ids = list(provenance.source_record_ids)
        if not ids:
            return []
        files = self._partition_files("raw_market_events", None, None)
        if not files:
            return []
        connection = duckdb.connect()
        try:
            connection.execute("SET TimeZone='UTC'")
            relation = connection.execute(
                "SELECT * FROM read_parquet(?, union_by_name=true, hive_partitioning=false) "
                "WHERE event_id IN (SELECT unnest(?::VARCHAR[]))",
                [[str(path) for path in files], ids],
            )
            column_names = [description[0] for description in relation.description]
            rows = [
                dict(zip(column_names, values, strict=True)) for values in relation.fetchall()
            ]
        finally:
            connection.close()
        spec = spec_for_table("raw_market_events")
        return [from_row(spec.contract, row) for row in rows]
