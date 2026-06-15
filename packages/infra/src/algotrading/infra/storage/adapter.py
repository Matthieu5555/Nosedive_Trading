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

from .compaction import is_compacted_file
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
            identical, conflicting = self._partition_collisions(table, spec, records, existing)
            if conflicting:
                # A re-write under an existing primary key with DIFFERENT content is a genuine
                # immutability breach — report the first such incoming record deterministically.
                for record in records:
                    key = primary_key_of(table, record)
                    if key in conflicting:
                        raise AppendOnlyViolation(table, key)
            if identical:
                # A re-write of a BYTE-IDENTICAL row (same key, same content) is an idempotent
                # no-op: the row is already persisted, so re-asserting it must neither duplicate it
                # nor raise. This is what lets a same-day re-fire converge (ADR 0032 overwrite-by-
                # re-run) on the append-only tables too — drop the already-present rows before the
                # concat, so an intraday capture re-run (or a close after one) is a clean no-op
                # rather than an AppendOnlyViolation. A same-key row with changed content still
                # raises above; immutability (ADR 0040/0015) holds (identical == no mutation).
                new_rows = [
                    row
                    for record, row in zip(records, new_rows, strict=True)
                    if primary_key_of(table, record) not in identical
                ]
                new_table = _rows_to_arrow(new_rows, schema)
            new_table = pa.concat_tables([existing, new_table])

        return new_table

    @staticmethod
    def _partition_collisions(
        table: str, spec: TableSpec, records: list[object], existing: pa.Table
    ) -> tuple[set[tuple[object, ...]], set[tuple[object, ...]]]:
        """Split the append-only primary-key collisions into ``(identical, conflicting)``.

        A DuckDB ``SEMI JOIN`` of the existing partition against the incoming keys on the
        *whole* composite primary key (every key column at once, on the engine's native typed
        values) selects exactly the existing rows whose key an incoming record re-asserts — a
        row collides only on its full key, never because it shares one key field. Each colliding
        existing row is reconstructed to its typed record (:func:`from_row`, the same path
        :meth:`read` uses) and compared *by value* with the incoming record:

        * **identical** — equal records: a byte-identical re-write, an idempotent no-op the
          caller drops (re-asserting an immutable row must not duplicate it nor raise);
        * **conflicting** — same key, different content: a genuine append-only/immutability
          breach the caller raises on.

        The typed compare is immune to storage-encoding asymmetries (JSON key order, datetime
        tz) because both sides pass through the same (de)serialization. (Caveat: a NaN float
        field would compare unequal to itself and read as a false conflict — none of the current
        append-only contracts carry computed floats; a future one that does needs an explicit
        NaN-aware compare.) Returns native value tuples (the form :func:`primary_key_of` yields);
        both sets empty when nothing collides.
        """
        key_columns = list(spec.primary_key)
        incoming = pa.table(
            {name: [getattr(record, name) for record in records] for name in key_columns},
            schema=pa.schema([existing.schema.field(name) for name in key_columns]),
        )
        connection = duckdb.connect()
        try:
            connection.execute("SET TimeZone='UTC'")
            connection.register("existing_rows", existing)
            connection.register("incoming_keys", incoming)
            using = ", ".join(key_columns)
            relation = connection.execute(
                f"SELECT * FROM existing_rows SEMI JOIN incoming_keys USING ({using})"
            )
            column_names = [description[0] for description in relation.description]
            existing_rows = [
                dict(zip(column_names, values, strict=True)) for values in relation.fetchall()
            ]
        finally:
            connection.close()
        existing_by_key = {
            primary_key_of(table, record): record
            for record in (from_row(spec.contract, row) for row in existing_rows)
        }
        identical: set[tuple[object, ...]] = set()
        conflicting: set[tuple[object, ...]] = set()
        for record in records:
            key = primary_key_of(table, record)
            existing_record = existing_by_key.get(key)
            if existing_record is None:
                continue
            if record == existing_record:
                identical.add(key)
            else:
                conflicting.add(key)
        return identical, conflicting

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
    def underlyings_present(self, table: str, *, provider: str | None = None) -> frozenset[str]:
        """Every ``underlying=<SYM>`` partition name present for *table*, as a set.

        A filesystem walk over the partition *directory names* — no Parquet file is
        opened — so a sweep deciding "skip what is already on disk" pays one cheap scan
        instead of a full-table read per ticker (the ohlc-backfill stall). ``provider``
        narrows a provider-partitioned table to one source segment; left ``None`` it
        unions across every provider. An absent table or provider segment is an empty
        set, never an error.
        """
        spec = spec_for_table(table)
        base = table_dir(self.root, table)
        if not base.exists():
            return frozenset()
        if spec.provider_partitioned:
            roots = (
                [base / f"provider={provider}"]
                if provider is not None
                else [
                    p
                    for p in base.iterdir()
                    if p.is_dir() and p.name.startswith("provider=")
                ]
            )
        else:
            roots = [base]
        names: set[str] = set()
        for segment_root in roots:
            if not segment_root.exists():
                continue
            for date_dir in segment_root.iterdir():
                if not date_dir.is_dir() or not date_dir.name.startswith("trade_date="):
                    continue
                for underlying_dir in date_dir.iterdir():
                    if underlying_dir.is_dir() and underlying_dir.name.startswith("underlying="):
                        names.add(underlying_dir.name.split("=", 1)[1])
        return frozenset(names)

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

    def _cold_files_for(
        self,
        table: str,
        spec: Any,
        underlying: str | None,
        provider: str | None,
    ) -> list[Path]:
        """Return existing cold (compacted) Parquet files for a cold-compactable table.

        Cold files sit at ``provider=<P>/underlying=<SYM>/data.parquet`` — the ADR 0034 §3
        layout where ``trade_date`` is a column, not a directory segment.  When ``underlying``
        or ``provider`` are given, only that subset is returned.  An absent directory or no
        cold files yields an empty list.

        This method is only meaningful for tables with ``cold_compactable=True`` (currently
        only ``daily_bar``).  It is not called for other tables.
        """
        base = table_dir(self.root, table)
        if not base.exists():
            return []

        if spec.provider_partitioned and provider is not None:
            provider_dirs: list[Path] = [base / f"provider={provider}"]
        elif spec.provider_partitioned:
            provider_dirs = [
                p for p in base.iterdir() if p.is_dir() and p.name.startswith("provider=")
            ]
        else:
            provider_dirs = [base]

        files: list[Path] = []
        for prov_dir in provider_dirs:
            if not prov_dir.exists():
                continue
            if underlying is not None:
                candidate = prov_dir / f"underlying={underlying}" / "data.parquet"
                if candidate.exists() and is_compacted_file(candidate):
                    files.append(candidate)
            else:
                for und_dir in prov_dir.iterdir():
                    if not und_dir.is_dir() or not und_dir.name.startswith("underlying="):
                        continue
                    candidate = und_dir / "data.parquet"
                    if candidate.exists() and is_compacted_file(candidate):
                        files.append(candidate)
        return sorted(files)

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

        For ``cold_compactable`` tables (``daily_bar``), cold compacted files
        (``provider=<P>/underlying=<SYM>/data.parquet``) are collected alongside hot
        per-day files and appended to the returned list so the read path unions both.
        The caller (``read``) handles deduplication on the primary key.
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
            hot_files = [single] if single.exists() else []
            if spec.cold_compactable:
                cold_files = self._cold_files_for(table, spec, underlying, provider)
                return sorted(set(hot_files + cold_files))
            return hot_files

        # For short date ranges prefer a per-day stat walk over a recursive glob.
        if start_date is not None and end_date is not None:
            delta = end_date - start_date
            can_direct = (
                (underlying is not None and 0 <= delta.days <= 1826)
                or (underlying is None and 0 <= delta.days <= 31)
            )
            if can_direct:
                hot_files = self._files_by_date_range_direct(
                    table, spec, start_date, end_date, underlying, version, provider
                )
                if spec.cold_compactable:
                    cold_files = self._cold_files_for(table, spec, underlying, provider)
                    return sorted(set(hot_files + cold_files))
                return hot_files

        hot_files = self._files_by_glob(
            table, trade_date, underlying, version, provider, start_date, end_date
        )
        if spec.cold_compactable:
            cold_files = self._cold_files_for(table, spec, underlying, provider)
            return sorted(set(hot_files + cold_files))
        return hot_files

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

        For ``cold_compactable`` tables (``daily_bar``), the file list may contain both
        hot per-day files and a cold compacted file for the same ticker.  When cold files
        are present the query uses ``DISTINCT ON`` to deduplicate on the primary key —
        the hot row wins over the cold copy for any date that appears in both (the hot
        partition is written after the cold file was produced, so it is the more recent
        capture).  Date-range filtering is pushed into SQL when ``start_date``/``end_date``
        are given, so cold-file reads still benefit from DuckDB predicate pushdown.
        """
        spec = spec_for_table(table)
        files = self._partition_files(
            table, trade_date, underlying, version, provider, start_date, end_date
        )
        if not files:
            return []

        has_cold = spec.cold_compactable and any(is_compacted_file(f) for f in files)
        file_list = [str(path) for path in files]

        connection = duckdb.connect()
        try:
            connection.execute("SET TimeZone='UTC'")
            source = "read_parquet(?, union_by_name=true, hive_partitioning=false)"
            if has_cold:
                # Build WHERE clause for date-range pushdown onto the trade_date column
                # of the cold file (DuckDB row-group min/max stats = implicit date index).
                where_parts: list[str] = []
                params: list[object] = [file_list]
                if start_date is not None:
                    where_parts.append("trade_date >= ?")
                    params.append(start_date)
                if end_date is not None:
                    where_parts.append("trade_date <= ?")
                    params.append(end_date)
                where_clause = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
                pk_cols = ", ".join(spec.primary_key)
                # DISTINCT ON deduplicates: the cold file row loses to any hot row sharing
                # the same (provider, underlying, trade_date) primary key.  The ORDER BY
                # here is arbitrary among ties — both carry the same data when the
                # migration script ran correctly.
                query = (
                    f"SELECT DISTINCT ON ({pk_cols}) * "
                    f"FROM {source} {where_clause}"
                )
                relation = connection.execute(query, params)
            else:
                relation = connection.execute(
                    f"SELECT * FROM {source}",
                    [file_list],
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
