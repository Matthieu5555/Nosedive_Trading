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

import contextlib
import shutil
from collections import defaultdict
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from algotrading.infra.contracts.registry import TableSpec, spec_for_table
from algotrading.infra.contracts.validation import validate_record

from .errors import AppendOnlyViolation, DuplicateKeyInBatch, VersionedWriteNotAllowed
from .partitioning import (
    partition_dir,
    partition_file,
    provider_of,
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

        grouped: dict[tuple[str | None, date, str], list[object]] = defaultdict(list)
        for record in records:
            provider = provider_of(record) if spec.provider_partitioned else None
            grouped[(provider, trade_date_of(record), underlying_of(record))].append(record)

        schema = arrow_schema(spec.contract)
        prepared: list[tuple[Path, pa.Table]] = []
        for (provider, trade_date, underlying), partition_records in grouped.items():
            path = partition_file(
                self.root, table, trade_date, underlying, version, provider
            )
            new_table = self._prepare_partition(
                table, spec, schema, trade_date, underlying, partition_records, version, provider
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
        provider: str | None = None,
    ) -> pa.Table:
        """Build the Arrow table to land for one partition, touching no files.

        For an append-only table this reads the existing partition, rejects any
        primary-key collision, and returns the existing rows concatenated with the
        new ones. For other tables it returns just the new rows (replace
        semantics). It mutates nothing, so a collision is surfaced in the prepare
        phase, before any partition in the batch is committed.
        """
        path = partition_file(self.root, table, trade_date, underlying, version, provider)
        new_rows = [to_row(spec.contract, record) for record in records]
        new_table = _rows_to_arrow(new_rows, schema)

        if spec.append_only and path.exists():
            # Read only the file's own columns. trade_date and underlying are also
            # stored as real columns inside the file, so pyarrow must NOT re-infer
            # them from the Hive-style directory names: that produces dictionary
            # <string> columns that collide with the file's date32/string ones and
            # breaks the concat below. partitioning=None disables that inference.
            existing = pq.read_table(path, partitioning=None)
            colliding = self._existing_key_collisions(spec, records, existing)
            if colliding:
                # Report the first incoming record (in batch order) whose full
                # composite key already exists, so the rejection is deterministic
                # and names exactly the offending key, as the prior loop did.
                for record in records:
                    key = primary_key_of(table, record)
                    if key in colliding:
                        raise AppendOnlyViolation(table, key)
            new_table = pa.concat_tables([existing, new_table])

        return new_table

    @staticmethod
    def _existing_key_collisions(
        spec: TableSpec, records: list[object], existing: pa.Table
    ) -> set[tuple[object, ...]]:
        """The full composite primary keys of ``records`` already present in ``existing``.

        The collision test is a DuckDB ``SEMI JOIN`` of the incoming keys against the
        existing partition on the *whole* primary key (every key column at once, on the
        engine's native typed values), so a row collides only when its full composite key
        already exists — never because it merely shares one key field. This is the
        engine-native form of the append-only immutability check; the previous
        ``zip`` + Python-set membership it replaces decided the same set. Returns the
        colliding keys as native value tuples (the form :func:`primary_key_of` yields),
        empty when nothing collides.
        """
        key_columns = list(spec.primary_key)
        incoming = pa.table(
            {name: [getattr(record, name) for record in records] for name in key_columns},
            schema=pa.schema([existing.schema.field(name) for name in key_columns]),
        )
        connection = duckdb.connect()
        try:
            connection.execute("SET TimeZone='UTC'")
            connection.register("existing_keys", existing.select(key_columns))
            connection.register("incoming_keys", incoming)
            using = ", ".join(key_columns)
            rows = connection.execute(
                f"SELECT {using} FROM incoming_keys "
                f"SEMI JOIN existing_keys USING ({using})"
            ).fetchall()
        finally:
            connection.close()
        return {tuple(row) for row in rows}

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
    def _discover_providers(self, table: str, spec: Any) -> list[str | None]:
        """Return every provider segment present for *table*, or ``[None]`` if none."""
        base = table_dir(self.root, table)
        if not base.exists():
            return []
        return [
            p.name.split("=", 1)[1]
            for p in base.iterdir()
            if p.is_dir() and p.name.startswith("provider=")
        ]

    @staticmethod
    def _is_live_file(path: Path) -> bool:
        """True when *path* is a live (unversioned) Parquet file.

        A live file sits directly under ``underlying=<SYM>/data.parquet``.
        A restated file sits one level deeper under ``version=<V>/data.parquet``,
        so its parent directory name starts with ``"version="``.
        """
        return not path.parent.name.startswith("version=")

    def _files_by_date_range_direct(
        self,
        table: str,
        spec: Any,
        start_date: date,
        end_date: date,
        underlying: str | None,
        version: str | None,
        provider: str | None,
    ) -> list[Path]:
        """Collect Parquet files by walking dates one day at a time.

        Faster than a recursive glob for narrow ranges because it only stats the
        exact paths it expects rather than traversing the whole tree. Used when
        the date range is short enough to make the per-day stat budget acceptable:
        up to 5 years when ``underlying`` is fixed, up to 31 days otherwise.
        """
        if spec.provider_partitioned and provider is None:
            providers: list[str | None] = self._discover_providers(table, spec)
        else:
            providers = [provider]

        files: list[Path] = []
        curr = start_date
        while curr <= end_date:
            for p_val in providers:
                if underlying is not None:
                    path = partition_file(
                        self.root, table, curr, underlying, version, p_val
                    )
                    if path.exists():
                        files.append(path)
                else:
                    # No underlying given: iterate underlying subdirs for this date.
                    d_dir = table_dir(self.root, table)
                    if p_val is not None:
                        d_dir = d_dir / f"provider={p_val}"
                    d_dir = d_dir / f"trade_date={curr.isoformat()}"
                    if d_dir.exists():
                        if version is not None:
                            files.extend(
                                d_dir.glob(f"**/version={version}/data.parquet")
                            )
                        else:
                            files.extend(
                                p
                                for p in d_dir.glob("**/data.parquet")
                                if self._is_live_file(p)
                            )
            curr += timedelta(days=1)
        return sorted(files)

    def _files_by_glob(
        self,
        table: str,
        trade_date: date | None,
        underlying: str | None,
        version: str | None,
        provider: str | None,
        start_date: date | None,
        end_date: date | None,
    ) -> list[Path]:
        """Collect Parquet files via recursive glob, then filter by date/version.

        Used when the date range is too wide for the per-day stat approach, or
        when neither ``start_date`` nor ``end_date`` is given. Applies date-range
        filtering in a post-glob pass by parsing ``trade_date=<D>`` path segments.
        """
        base = table_dir(self.root, table)
        if provider is not None:
            base = base / f"provider={provider}"
        if not base.exists():
            return []

        if underlying is not None:
            files: list[Path] = sorted(
                base.glob(f"**/underlying={underlying}/**/*.parquet")
            )
        else:
            files = sorted(base.glob("**/*.parquet"))

        # Narrow to the requested trade_date when one is given (cross-provider
        # reads fall through here because the single-partition fast path is
        # skipped for provider-partitioned tables with provider=None).
        if trade_date is not None:
            segment = f"trade_date={trade_date.isoformat()}"
            files = [path for path in files if segment in path.parts]

        # Post-glob date-range filter: parse the trade_date= path segment.
        if start_date is not None or end_date is not None:
            filtered: list[Path] = []
            for path in files:
                dt_val: date | None = None
                for part in path.parts:
                    if part.startswith("trade_date="):
                        with contextlib.suppress(ValueError):
                            dt_val = date.fromisoformat(part.split("=", 1)[1])
                        break
                if dt_val is not None:
                    if start_date is not None and dt_val < start_date:
                        continue
                    if end_date is not None and dt_val > end_date:
                        continue
                filtered.append(path)
            files = filtered

        # Version filter: live rows sit directly under underlying=<SYM>; a
        # restatement file sits one level deeper under version=<V>.
        if version is None:
            return [path for path in files if self._is_live_file(path)]
        return [path for path in files if path.parent.name == f"version={version}"]

    def _partition_files(
        self,
        table: str,
        trade_date: date | None,
        underlying: str | None,
        version: str | None = None,
        provider: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[Path]:
        """The Parquet files a read covers — live rows and restatements never mix.

        ``version=None`` selects only *live* (unversioned) files — those that sit
        directly under ``underlying=<SYM>``, never one nested in a ``version=<V>``
        restatement sub-partition. An explicit ``version`` selects only that
        restatement's files. So the live partition and a restatement of it, which
        coexist on disk for the same ``(trade_date, underlying)``, are read back
        separately and a version-blind read can never double-count overlapping keys.

        ``provider`` narrows a provider-partitioned read to one source's segment; left
        ``None`` a table-wide read globs across every provider, which is correct for the
        non-provider-partitioned tables (no provider segment exists) and a deliberate
        cross-source scan for provider-partitioned ones.
        """
        spec = spec_for_table(table)
        if (
            trade_date is not None
            and underlying is not None
            and not (spec.provider_partitioned and provider is None)
        ):
            # One partition: exactly the live file (version=None) or exactly the one
            # restatement file (version=<V>). partition_file places each precisely.
            # Skipped when the table is provider-partitioned and no provider was given:
            # partition_file would then build a path WITHOUT the provider=<P> segment,
            # which never exists on disk, so the read would silently return [] — an
            # under-specified read masquerading as "no data". Such a read falls through
            # to the cross-provider glob below, matching the documented behaviour used
            # when trade_date/underlying are absent: union every provider's segment for
            # this (trade_date, underlying).
            single = partition_file(
                self.root, table, trade_date, underlying, version, provider
            )
            return [single] if single.exists() else []

        # For short date ranges prefer a per-day stat walk over a recursive glob.
        if start_date is not None and end_date is not None:
            delta = end_date - start_date
            can_direct = (
                (underlying is not None and 0 <= delta.days <= 1826)
                or (underlying is None and 0 <= delta.days <= 31)
            )
            if can_direct:
                return self._files_by_date_range_direct(
                    table, spec, start_date, end_date, underlying, version, provider
                )

        return self._files_by_glob(
            table, trade_date, underlying, version, provider, start_date, end_date
        )

    def read(
        self,
        table: str,
        *,
        trade_date: date | None = None,
        underlying: str | None = None,
        version: str | None = None,
        provider: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
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

        ``provider`` narrows a provider-partitioned read to one source; left ``None`` it
        reads across every provider (the only behaviour for non-provider-partitioned
        tables, which have no provider segment).
        """
        spec = spec_for_table(table)
        files = self._partition_files(
            table, trade_date, underlying, version, provider, start_date, end_date
        )
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
        """List the (trade_date, underlying) partitions present for a table.

        For a provider-partitioned table the same ``(trade_date, underlying)`` may exist
        under more than one provider; this returns the de-duplicated set across providers,
        keeping the legacy two-tuple shape.
        """
        base = table_dir(self.root, table)
        if not base.exists():
            return []
        spec = spec_for_table(table)
        date_roots = (
            sorted(base.glob("provider=*")) if spec.provider_partitioned else [base]
        )
        found: list[tuple[date, str]] = []
        seen: set[tuple[date, str]] = set()
        for date_root in date_roots:
            for date_dir in sorted(date_root.glob("trade_date=*")):
                trade_date = date.fromisoformat(date_dir.name.split("=", 1)[1])
                for underlying_dir in sorted(date_dir.glob("underlying=*")):
                    underlying = underlying_dir.name.split("=", 1)[1]
                    key = (trade_date, underlying)
                    if key not in seen:
                        seen.add(key)
                        found.append(key)
        return found

    def list_versions(
        self, table: str, trade_date: date, underlying: str, provider: str | None = None
    ) -> list[str]:
        """List the ``version=<V>`` sub-partitions present for one partition.

        Returns the version strings (sorted), empty when the partition is unversioned
        or absent. This is how a restatement test asserts that a newer-code run landed
        a new version beside the older one rather than overwriting it. ``provider`` selects
        the source segment for a provider-partitioned table.
        """
        partition = partition_dir(
            self.root, table, trade_date, underlying, None, provider
        )
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
        provider: str | None = None,
    ) -> None:
        """Delete one partition. Idempotent: deleting a missing partition is fine.

        With ``version=None`` the whole ``(trade_date, underlying)`` partition is
        removed, including any version sub-partitions; with a version only that one
        ``version=<V>`` sub-partition is removed, leaving the others intact. ``provider``
        selects the source segment for a provider-partitioned table.
        """
        target = partition_dir(
            self.root, table, trade_date, underlying, version, provider
        )
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
            matches = self._read_by_keys(source_table, keys)
            if matches:
                resolved[source_table] = matches
        return resolved

    def _read_by_keys(
        self, table: str, keys: set[tuple[str, ...]]
    ) -> list[Any]:
        """Read exactly the records of ``table`` whose full primary key is in ``keys``.

        ``keys`` are canonical primary-key tuples (the string form
        :func:`canonical_primary_key` produces). The match is pushed into DuckDB as a
        ``WHERE (pk…) IN (…)`` row predicate so a year-scale source table is never
        materialized in full just to keep a handful of rows — but it stays the *full*
        composite-key match, so a reference to one raw event never pulls back another
        that merely shares a single key field. Each canonical-string key component is
        cast to its parquet column's own type inside the row list, so DATE/TIMESTAMPTZ
        keys compare on parsed values (exact across the microsecond boundary), not on a
        re-derived string form. An empty key set, or no matching rows, yields an empty
        list.
        """
        spec = spec_for_table(table)
        files = self._partition_files(table, None, None, None, None)
        if not keys or not files:
            return []
        file_list = [str(path) for path in files]
        key_columns = spec.primary_key
        connection = duckdb.connect()
        try:
            connection.execute("SET TimeZone='UTC'")
            source = "read_parquet(?, union_by_name=true, hive_partitioning=false)"
            column_types = {
                row[0]: row[1]
                for row in connection.execute(
                    f"DESCRIBE SELECT * FROM {source}", [file_list]
                ).fetchall()
            }
            # One row of casts per wanted key. Each canonical-string component is cast to
            # its own parquet column type, so DATE/TIMESTAMPTZ keys compare on parsed
            # values rather than on a re-derived string (exact across the microsecond
            # boundary); VARCHAR casts are identity.
            row_casts = "(" + ", ".join(
                f"CAST(? AS {column_types[name]})" for name in key_columns
            ) + ")"
            in_list = ", ".join(row_casts for _ in keys)
            key_tuple = ", ".join(key_columns)
            params: list[object] = [file_list]
            for key in keys:
                params.extend(key)
            relation = connection.execute(
                f"SELECT * FROM {source} WHERE ({key_tuple}) IN ({in_list})",
                params,
            )
            column_names = [description[0] for description in relation.description]
            rows = [
                dict(zip(column_names, values, strict=True))
                for values in relation.fetchall()
            ]
        finally:
            connection.close()
        return [from_row(spec.contract, row) for row in rows]

    def raw_events_for(self, derived_record: object) -> list[Any]:
        """Return the raw events that produced a derived record.

        The raw-market-events slice of :meth:`source_records_for` — the headline
        "which raw records produced this?" lineage question. Returns an empty list
        when the record has no raw-event lineage.
        """
        return self.source_records_for(derived_record).get("raw_market_events", [])
