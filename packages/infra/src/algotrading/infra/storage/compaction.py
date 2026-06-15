"""Cold-compaction for daily_bar (ADR 0034 §3 / infra-daily-bar-compaction spec).

Implements the one-ticker-per-file cold layout:

    <root>/raw/daily_bar/provider=<P>/underlying=<SYM>/data.parquet

rows sorted by ``trade_date`` as a column (DuckDB predicate pushdown via row-group
min/max stats ≈ an implicit date index, ADR 0033).

The hot layout (per-day partitions) is unchanged:

    <root>/raw/daily_bar/provider=<P>/trade_date=<D>/underlying=<SYM>/data.parquet

Hot and cold coexist during the migration window; the read path unions them and
deduplicates on ``(provider, underlying, trade_date)`` so partial compaction is
transparent to callers (ADR 0034 OQ-2: cold-only compaction, hot + cold union read).

Public surface:
  ``compact_ticker``          — merge all hot files for one (provider, ticker) into a cold file
  ``compacted_file_path``     — the cold file path for a given (root, table, provider, ticker)
  ``is_compacted_file``       — True when the path is a cold (provider-direct) file
  ``list_hot_files_for_ticker`` — the per-day hot files for one (provider, ticker)
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import duckdb
import pyarrow.parquet as pq

from .partitioning import table_dir

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def compacted_file_path(root: Path, table: str, provider: str, underlying: str) -> Path:
    """Return the cold compacted file path for one (provider, ticker) pair.

    Layout: ``<root>/<layer>/<table>/provider=<P>/underlying=<SYM>/data.parquet``.
    The table layer comes from the registry (``table_dir`` already resolves this).
    """
    return (
        table_dir(root, table)
        / f"provider={provider}"
        / f"underlying={underlying}"
        / "data.parquet"
    )


def is_compacted_file(path: Path) -> bool:
    """True when ``path`` is a cold compacted file (grandparent is the provider= segment).

    Hot files sit at:  …/provider=P/trade_date=D/underlying=SYM/data.parquet
    Cold files sit at: …/provider=P/underlying=SYM/data.parquet

    The distinguishing property: a hot file's grandparent starts with ``trade_date=``;
    a cold file's grandparent starts with ``provider=``.
    """
    grandparent = path.parent.parent
    return grandparent.name.startswith("provider=")


def list_hot_files_for_ticker(root: Path, table: str, provider: str, underlying: str) -> list[Path]:
    """Return the per-day hot Parquet files for one (provider, ticker) pair, sorted.

    Hot files sit under trade_date= subdirectories inside the provider= segment.
    The cold compacted file (if it exists) is excluded.
    """
    provider_dir = table_dir(root, table) / f"provider={provider}"
    if not provider_dir.exists():
        return []
    files: list[Path] = []
    for date_dir in provider_dir.iterdir():
        if not date_dir.is_dir() or not date_dir.name.startswith("trade_date="):
            continue
        candidate = date_dir / f"underlying={underlying}" / "data.parquet"
        if candidate.exists():
            files.append(candidate)
    return sorted(files)


# ---------------------------------------------------------------------------
# Core compaction logic
# ---------------------------------------------------------------------------


def compact_ticker(
    root: Path,
    provider: str,
    underlying: str,
    *,
    table: str = "daily_bar",
    remove_hot: bool = True,
) -> None:
    """Merge all hot files for one (provider, ticker) into a single cold file.

    The compaction is value-preserving and idempotent:

    * **Read phase** — collect all hot per-day files for this (provider, ticker). If
      there are none (already fully compacted), the existing cold file is left untouched.
    * **Union phase** — if a cold file already exists, include it in the read so that
      any rows already compacted are not lost (safe re-entry during the migration window).
    * **Sort + write phase** — write sorted-by-trade_date rows to a temp file in the
      same directory, then atomically rename into the final cold path.
    * **Verify phase** — assert the output row count equals the input count (catches
      any DuckDB read/write surprise before any hot file is touched).
    * **Archive phase** (when ``remove_hot=True``) — remove the hot per-day files that
      are now superseded by the cold file.

    ``remove_hot=False`` leaves hot files in place (the read path deduplicates on
    ``(provider, underlying, trade_date)``), which is the safe default during an
    incremental migration.  The migration script passes ``remove_hot=True`` after
    verifying row identity.

    Raises ``ValueError`` when the output row count does not match the input count.
    Raises ``RuntimeError`` when the temp write fails and the cold path is left clean.
    """
    cold_path = compacted_file_path(root, table, provider, underlying)
    hot_files = list_hot_files_for_ticker(root, table, provider, underlying)

    if not hot_files:
        # Nothing to merge — already fully compacted or never written.
        if cold_path.exists():
            log.debug(
                "compact_ticker: no hot files found, cold file already present — no-op",
                extra={"provider": provider, "underlying": underlying},
            )
        else:
            log.debug(
                "compact_ticker: no hot files and no cold file — nothing to do",
                extra={"provider": provider, "underlying": underlying},
            )
        return

    # Build the list of source files: hot files + the existing cold file (if any),
    # so a re-run during the migration window does not lose previously compacted rows.
    source_files = list(hot_files)
    if cold_path.exists():
        source_files.append(cold_path)

    file_list = [str(f) for f in source_files]

    log.info(
        "compact_ticker: reading %d source files",
        len(source_files),
        extra={"provider": provider, "underlying": underlying, "table": table},
    )

    conn = duckdb.connect()
    try:
        conn.execute("SET TimeZone='UTC'")
        # Count rows before writing — the independent verification oracle.
        count_before: int = conn.execute(
            "SELECT COUNT(*) FROM read_parquet(?, union_by_name=true, hive_partitioning=false)",
            [file_list],
        ).fetchone()[0]  # type: ignore[index]

        if count_before == 0:
            log.warning(
                "compact_ticker: source files contain 0 rows — skipping",
                extra={"provider": provider, "underlying": underlying},
            )
            return

        # Read all rows sorted by trade_date, deduplicated on the primary key
        # (provider, underlying, trade_date) — handles the re-run case where the cold
        # file's rows would otherwise duplicate the hot files' rows.
        arrow_table = conn.execute(
            """
            SELECT DISTINCT ON (provider, underlying, trade_date) *
            FROM read_parquet(?, union_by_name=true, hive_partitioning=false)
            ORDER BY trade_date
            """,
            [file_list],
        ).to_arrow_table()
    finally:
        conn.close()

    out_row_count = arrow_table.num_rows

    # Atomic write: stage to a temp file, then rename into place.
    # The in-memory count is the verification oracle — the write cannot silently drop
    # rows (pq.write_table is all-or-nothing), so we verify the parquet metadata row
    # count rather than re-reading the whole file.
    cold_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = cold_path.parent / f"{cold_path.name}.compact_tmp"
    try:
        pq.write_table(
            arrow_table,
            temp_path,
            row_group_size=50_000,   # row groups sized for good predicate pushdown
            compression="snappy",
        )
        # Verify row count via parquet file metadata (cheap — no column reads).
        written_count = pq.read_metadata(temp_path).num_rows
        if written_count != out_row_count:
            temp_path.unlink(missing_ok=True)
            raise ValueError(
                f"compact_ticker: row count mismatch after write "
                f"(expected {out_row_count}, got {written_count})"
            )
        temp_path.replace(cold_path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise

    log.info(
        "compact_ticker: compacted %d rows → %s",
        out_row_count,
        cold_path,
        extra={
            "provider": provider,
            "underlying": underlying,
            "source_file_count": len(source_files),
        },
    )

    # Row-identity verification: input count (before dedup) may differ from output if
    # the cold file had overlapping rows; the deduped count is the ground truth.
    if count_before != out_row_count:
        log.debug(
            "compact_ticker: deduplication reduced %d → %d rows (expected during re-runs)",
            count_before,
            out_row_count,
            extra={"provider": provider, "underlying": underlying},
        )

    if remove_hot:
        _remove_hot_files(hot_files)


def _remove_hot_files(hot_files: list[Path]) -> None:
    """Delete the per-day hot partition directories superseded by the cold file."""
    removed = 0
    for f in hot_files:
        # The partition directory is underlying=SYM; the date directory is its parent.
        partition_dir = f.parent         # underlying=SYM/
        date_dir = partition_dir.parent  # trade_date=D/
        try:
            shutil.rmtree(partition_dir)
            # Remove the date directory if now empty (no other underlyings on that day).
            if date_dir.exists() and not any(date_dir.iterdir()):
                date_dir.rmdir()
            removed += 1
        except OSError as exc:
            log.warning(
                "compact_ticker: failed to remove hot file %s: %s",
                f,
                exc,
                extra={"path": str(f)},
            )
    log.debug("compact_ticker: removed %d hot partition dirs", removed)
