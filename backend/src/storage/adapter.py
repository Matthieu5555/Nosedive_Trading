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

from contracts.registry import TableSpec, spec_for_table
from contracts.validation import validate_record
from provenance import canonical_primary_key

from .errors import AppendOnlyViolation, DuplicateKeyInBatch, VersionedWriteNotAllowed
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
    def write(
        self, table: str, records: Sequence[object], *, version: str | None = None
    ) -> None:
        """Validate and persist a batch of records for one table — all or nothing.

        Append-only tables reject any primary key that already exists on disk;
        other tables replace each touched partition with the records supplied for
        it (recompute semantics). Every record is validated, the batch is checked
        for internal duplicate keys, and every touched partition is fully prepared
        — including the append-only collision check — before a single byte is
        written. A failure in a later partition therefore cannot leave an earlier
        one already changed: nothing is committed until the whole batch is ready.

        ``version`` selects the ``version=<V>`` sub-partition for a restated analytic
        and defaults to ``None`` — the unversioned, replace-in-place *live* layout. A
        write under a new version lands beside the live partition rather than
        overwriting it, which is how a newer-code restatement preserves the older
        analytic (step 13). Versioning is for derived analytics only: a versioned write
        to an append-only table (raw events, instrument master) is refused with
        :class:`VersionedWriteNotAllowed`, because raw observations are immutable and
        have no restatement.
        """
        if not records:
            return

        spec = spec_for_table(table)
        if version is not None and spec.append_only:
            raise VersionedWriteNotAllowed(table, version)
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
        prepared: list[tuple[Path, pa.Table]] = []
        for (trade_date, underlying), partition_records in grouped.items():
            path = partition_file(self.root, table, trade_date, underlying, version)
            new_table = self._prepare_partition(
                table, spec, schema, trade_date, underlying, partition_records, version
            )
            prepared.append((path, new_table))
        self._commit(prepared)

    def _prepare_partition(
        self,
        table: str,
        spec: TableSpec,
        schema: pa.Schema,
        trade_date: date,
        underlying: str,
        records: list[object],
        version: str | None = None,
    ) -> pa.Table:
        """Build the Arrow table to land for one partition, touching no files.

        For an append-only table this reads the existing partition, rejects any
        primary-key collision, and returns the existing rows concatenated with the
        new ones. For other tables it returns just the new rows (replace
        semantics). It mutates nothing, so a collision is surfaced in the prepare
        phase, before any partition in the batch is committed.
        """
        path = partition_file(self.root, table, trade_date, underlying, version)
        new_rows = [to_row(spec.contract, record) for record in records]
        new_table = _rows_to_arrow(new_rows, schema)

        if spec.append_only and path.exists():
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

        return new_table

    def _commit(self, prepared: list[tuple[Path, pa.Table]]) -> None:
        """Write every prepared partition by staging to temp files, then renaming.

        Each partition is written to a temporary file first; only once every temp
        file is on disk are they renamed into place. So a failure during the write
        phase — a serialization error, a full disk — leaves the store exactly as it
        was: the temporaries are removed and no target file is touched. Renames are
        per-file atomic on the same filesystem; the one gap we do not claim to
        survive is a crash *between* the final renames.
        """
        staged: list[tuple[Path, Path]] = []
        committed = False
        try:
            for path, new_table in prepared:
                path.parent.mkdir(parents=True, exist_ok=True)
                temp = path.parent / f"{path.name}.tmp"
                # Record before writing, so a half-written temp is still cleaned up.
                staged.append((temp, path))
                pq.write_table(new_table, temp)
            for temp, path in staged:
                temp.replace(path)
            committed = True
        finally:
            # On any failure (write error, interrupt) drop the staged temporaries,
            # leaving every target file as it was. A rename already done has moved
            # its temp away, so unlinking it is a harmless no-op.
            if not committed:
                for temp, _ in staged:
                    temp.unlink(missing_ok=True)

    # -- reading ----------------------------------------------------------
    def _partition_files(
        self,
        table: str,
        trade_date: date | None,
        underlying: str | None,
        version: str | None = None,
    ) -> list[Path]:
        """The Parquet files a read covers — live rows and restatements never mix.

        ``version=None`` selects only *live* (unversioned) files — those that sit
        directly under ``underlying=<SYM>``, never one nested in a ``version=<V>``
        restatement sub-partition. An explicit ``version`` selects only that
        restatement's files. So the live partition and a restatement of it, which
        coexist on disk for the same ``(trade_date, underlying)``, are read back
        separately and a version-blind read can never double-count overlapping keys.
        """
        if trade_date is not None and underlying is not None:
            # One partition: exactly the live file (version=None) or exactly the one
            # restatement file (version=<V>). partition_file places each precisely.
            single = partition_file(self.root, table, trade_date, underlying, version)
            return [single] if single.exists() else []
        base = table_dir(self.root, table)
        if not base.exists():
            return []
        files = sorted(base.glob("**/*.parquet"))
        if version is None:
            # Live rows only: the file directly under underlying=<SYM>. A restatement
            # file lives one level deeper under version=<V>, so its parent dir name
            # starts with "version=" and is excluded here.
            return [path for path in files if not path.parent.name.startswith("version=")]
        return [path for path in files if path.parent.name == f"version={version}"]

    def read(
        self,
        table: str,
        *,
        trade_date: date | None = None,
        underlying: str | None = None,
        version: str | None = None,
    ) -> list[Any]:
        """Read records for a table (optionally one partition) back into contracts.

        Uses DuckDB over the Parquet files with ``union_by_name`` so partitions
        written under an older, narrower schema remain readable — missing columns
        come back as ``None``. ``version`` left ``None`` reads only the *live*
        (unversioned) rows — the default a caller almost always wants; an explicit
        ``version`` reads only that restatement. Live rows and restatements coexist
        on disk for the same partition (the live run writes unversioned, a
        reconstruction writes ``version=<V>`` beside it), so this separation is what
        keeps a default read from returning both and double-counting overlapping
        primary keys. To inspect restatements, enumerate them with
        :meth:`list_versions` and read each by its explicit version.
        """
        spec = spec_for_table(table)
        files = self._partition_files(table, trade_date, underlying, version)
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

    def list_versions(
        self, table: str, trade_date: date, underlying: str
    ) -> list[str]:
        """List the ``version=<V>`` sub-partitions present for one partition.

        Returns the version strings (sorted), empty when the partition is unversioned
        or absent. This is how a restatement test asserts that a newer-code run landed
        a new version beside the older one rather than overwriting it.
        """
        partition = partition_dir(self.root, table, trade_date, underlying)
        if not partition.exists():
            return []
        return sorted(
            child.name.split("=", 1)[1]
            for child in partition.glob("version=*")
            if child.is_dir()
        )

    def delete_partition(
        self,
        table: str,
        trade_date: date,
        underlying: str,
        version: str | None = None,
    ) -> None:
        """Delete one partition. Idempotent: deleting a missing partition is fine.

        With ``version=None`` the whole ``(trade_date, underlying)`` partition is
        removed, including any version sub-partitions; with a version only that one
        ``version=<V>`` sub-partition is removed, leaving the others intact.
        """
        target = partition_dir(self.root, table, trade_date, underlying, version)
        if target.exists():
            shutil.rmtree(target)

    # -- lineage ----------------------------------------------------------
    def source_records_for(self, record: object) -> dict[str, list[Any]]:
        """Return the source records that produced a record, grouped by table.

        Reads the typed source references off the record's provenance stamp and
        resolves each one by its *full* primary key, so a reference to one raw
        event never pulls back another that merely shares a single key field — a
        different session with the same event id, say. This is the deep lineage
        question ("which source records, in any table, produced this?"); tables
        with no match are omitted, and a record with no provenance resolves to an
        empty mapping.
        """
        provenance = getattr(record, "provenance", None)
        if provenance is None:
            return {}
        wanted: dict[str, set[tuple[str, ...]]] = defaultdict(set)
        for ref in provenance.source_records:
            wanted[ref.table].add(tuple(ref.primary_key))
        resolved: dict[str, list[Any]] = {}
        for source_table, keys in wanted.items():
            matches = [
                candidate
                for candidate in self.read(source_table)
                if canonical_primary_key(primary_key_of(source_table, candidate)) in keys
            ]
            if matches:
                resolved[source_table] = matches
        return resolved

    def raw_events_for(self, derived_record: object) -> list[Any]:
        """Return the raw events that produced a derived record.

        The raw-market-events slice of :meth:`source_records_for` — the headline
        "which raw records produced this?" lineage question. Returns an empty list
        when the record has no raw-event lineage.
        """
        return self.source_records_for(derived_record).get("raw_market_events", [])
