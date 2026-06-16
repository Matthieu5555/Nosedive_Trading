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
    ADHOC_RUN,
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
    spec = spec_for_table(table)
    return tuple(getattr(record, name) for name in spec.primary_key)


def _rows_to_arrow(rows: list[dict[str, Any]], schema: pa.Schema) -> pa.Table:
    columns = {name: [row.get(name) for row in rows] for name in schema.names}
    return pa.table(columns, schema=schema)


class ParquetStore:

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def write(
        self,
        table: str,
        records: Sequence[object],
        *,
        version: str | None = None,
        run_id: str | None = None,
    ) -> None:
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
                self.root, table, trade_date, underlying, version, provider, run_id
            )
            new_table = self._prepare_partition(
                table, spec, schema, trade_date, underlying, partition_records, version,
                provider, run_id,
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
        run_id: str | None = None,
    ) -> pa.Table:
        path = partition_file(
            self.root, table, trade_date, underlying, version, provider, run_id
        )
        new_rows = [to_row(spec.contract, record) for record in records]
        new_table = _rows_to_arrow(new_rows, schema)

        if spec.append_only and path.exists():
            existing = pq.read_table(path, partitioning=None)
            identical, conflicting = self._partition_collisions(table, spec, records, existing)
            if conflicting:
                for record in records:
                    key = primary_key_of(table, record)
                    if key in conflicting:
                        raise AppendOnlyViolation(table, key)
            if identical:
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
        staged: list[tuple[Path, Path]] = []
        committed = False
        try:
            for path, new_table in prepared:
                path.parent.mkdir(parents=True, exist_ok=True)
                temp = path.parent / f"{path.name}.tmp"
                staged.append((temp, path))
                pq.write_table(new_table, temp)
            for temp, path in staged:
                temp.replace(path)
            committed = True
        finally:
            if not committed:
                for temp, _ in staged:
                    temp.unlink(missing_ok=True)

    def underlyings_present(self, table: str, *, provider: str | None = None) -> frozenset[str]:
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
                # ``underlying=`` sits directly under the date for legacy tables, or one level
                # deeper (under ``run=``) for run-partitioned tables — glob spans both.
                for underlying_dir in date_dir.glob("**/underlying=*"):
                    if underlying_dir.is_dir():
                        names.add(underlying_dir.name.split("=", 1)[1])
        return frozenset(names)

    def _discover_providers(self, table: str, spec: Any) -> list[str | None]:
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

        if trade_date is not None:
            segment = f"trade_date={trade_date.isoformat()}"
            files = [path for path in files if segment in path.parts]

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

    @staticmethod
    def _filter_runs(
        files: list[Path], spec: TableSpec, run_id: str | None
    ) -> list[Path]:
        """Resolve which fetch's files to surface for a run-partitioned table.

        Run-partitioned files live at ``.../trade_date=<d>/run=<rid>/underlying=<u>/data.parquet``.
        With an explicit ``run_id`` we keep only that fetch's files. With ``run_id=None`` (the
        default) we keep, per (provider, trade_date, underlying) group, only the files under the
        newest ``run=`` directory by mtime — so the rest of the platform sees the latest fetch for
        a day, exactly as it did before run-partitioning, while older fetches stay on disk.
        """
        if not spec.run_partitioned:
            return files

        def split(path: Path) -> tuple[int | None, tuple[str, ...], Path | None]:
            parts = path.parts
            idx = next(
                (i for i, part in enumerate(parts) if part.startswith("run=")), None
            )
            if idx is None:
                return None, parts, None
            group = parts[:idx] + parts[idx + 1:]
            run_dir = Path(*parts[: idx + 1])
            return idx, group, run_dir

        if run_id is not None:
            wanted = f"run={run_id}"
            return [path for path in files if wanted in path.parts]

        newest: dict[tuple[str, ...], tuple[float, Path]] = {}
        for path in files:
            _idx, group, run_dir = split(path)
            if run_dir is None:
                continue
            mtime = run_dir.stat().st_mtime
            current = newest.get(group)
            if current is None or mtime > current[0]:
                newest[group] = (mtime, run_dir)

        kept: list[Path] = []
        for path in files:
            _idx, group, run_dir = split(path)
            if run_dir is None or (group in newest and run_dir == newest[group][1]):
                kept.append(path)
        return kept

    def _partition_files(
        self,
        table: str,
        trade_date: date | None,
        underlying: str | None,
        version: str | None = None,
        provider: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        run_id: str | None = None,
    ) -> list[Path]:
        spec = spec_for_table(table)
        if spec.run_partitioned:
            # The ``run=`` level sits between trade_date and underlying, so the single-file and
            # date-range-direct fast paths (which assume trade_date/underlying are adjacent) don't
            # apply; glob discovery walks the run level, then _filter_runs picks the right fetch.
            files = self._files_by_glob(
                table, trade_date, underlying, version, provider, start_date, end_date
            )
            return self._filter_runs(files, spec, run_id)
        if (
            trade_date is not None
            and underlying is not None
            and not (spec.provider_partitioned and provider is None)
        ):
            single = partition_file(
                self.root, table, trade_date, underlying, version, provider
            )
            hot_files = [single] if single.exists() else []
            if spec.cold_compactable:
                cold_files = self._cold_files_for(table, spec, underlying, provider)
                return sorted(set(hot_files + cold_files))
            return hot_files

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
        run_id: str | None = None,
    ) -> list[Any]:
        spec = spec_for_table(table)
        files = self._partition_files(
            table, trade_date, underlying, version, provider, start_date, end_date, run_id
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

    def list_partitions(self, table: str) -> list[tuple[date, str]]:
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
                # ``underlying=`` is directly under the date for legacy tables, or under ``run=``
                # for run-partitioned ones; ``**`` spans both and we de-dup across runs below.
                for underlying_dir in sorted(date_dir.glob("**/underlying=*")):
                    underlying = underlying_dir.name.split("=", 1)[1]
                    key = (trade_date, underlying)
                    if key not in seen:
                        seen.add(key)
                        found.append(key)
        return found

    def runs_for(
        self, table: str, trade_date: date, *, provider: str | None = None
    ) -> list[str]:
        """Run ids that have data on disk for a run-partitioned table on a trade date.

        Returns the ``run=`` segment values present under ``trade_date=<trade_date>``, newest
        first by directory mtime. Empty when the table is not run-partitioned, or when its data
        predates run-partitioning (the legacy flat layout has no ``run=`` level) — callers read
        that as "no per-run handle here, address this date by the date itself". This is the
        truth a picker must gate on: only a run id with a ``run=`` directory actually resolves
        to data, so listing ledger run ids without a partition would offer dead selections.

        The ``_adhoc`` catch-all (writes made without a run id, e.g. backfills) is excluded: it
        is not a fetch identity, carries no run-state ledger entry, and stays reachable as the
        default newest-fetch read — so a date holding only ``_adhoc`` data reads as date-only.
        """
        spec = spec_for_table(table)
        if not spec.run_partitioned:
            return []
        base = table_dir(self.root, table)
        if not base.exists():
            return []
        if provider is not None:
            date_roots = [base / f"provider={provider}"]
        elif spec.provider_partitioned:
            date_roots = [
                p for p in base.iterdir() if p.is_dir() and p.name.startswith("provider=")
            ]
        else:
            date_roots = [base]
        newest: dict[str, float] = {}
        segment = f"trade_date={trade_date.isoformat()}"
        for date_root in date_roots:
            date_dir = date_root / segment
            if not date_dir.is_dir():
                continue
            for run_dir in date_dir.glob("run=*"):
                if not run_dir.is_dir():
                    continue
                run_id = run_dir.name.split("=", 1)[1]
                if run_id == ADHOC_RUN:
                    continue
                mtime = run_dir.stat().st_mtime
                if run_id not in newest or mtime > newest[run_id]:
                    newest[run_id] = mtime
        return [run_id for run_id, _ in sorted(newest.items(), key=lambda kv: -kv[1])]

    def list_versions(
        self, table: str, trade_date: date, underlying: str, provider: str | None = None
    ) -> list[str]:
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
        target = partition_dir(
            self.root, table, trade_date, underlying, version, provider
        )
        if target.exists():
            shutil.rmtree(target)

    def source_records_for(self, record: object) -> dict[str, list[Any]]:
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
        return self.source_records_for(derived_record).get("raw_market_events", [])
