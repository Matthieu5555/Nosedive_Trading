from __future__ import annotations

import logging
import shutil
from pathlib import Path

import duckdb
import pyarrow.parquet as pq

from .partitioning import table_dir

log = logging.getLogger(__name__)


def compacted_file_path(root: Path, table: str, provider: str, underlying: str) -> Path:
    return (
        table_dir(root, table)
        / f"provider={provider}"
        / f"underlying={underlying}"
        / "data.parquet"
    )


def is_compacted_file(path: Path) -> bool:
    grandparent = path.parent.parent
    return grandparent.name.startswith("provider=")


def list_hot_files_for_ticker(root: Path, table: str, provider: str, underlying: str) -> list[Path]:
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


def compact_ticker(
    root: Path,
    provider: str,
    underlying: str,
    *,
    table: str = "daily_bar",
    remove_hot: bool = True,
) -> None:
    cold_path = compacted_file_path(root, table, provider, underlying)
    hot_files = list_hot_files_for_ticker(root, table, provider, underlying)

    if not hot_files:
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

    cold_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = cold_path.parent / f"{cold_path.name}.compact_tmp"
    try:
        pq.write_table(
            arrow_table,
            temp_path,
            row_group_size=50_000,
            compression="snappy",
        )
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
    removed = 0
    for f in hot_files:
        partition_dir = f.parent
        date_dir = partition_dir.parent
        try:
            shutil.rmtree(partition_dir)
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
