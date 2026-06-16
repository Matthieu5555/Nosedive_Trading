"""Purge the non-raw layer for a trade date and replay it from the immutable raw keystone.

Raw is Tier-1 (ADR 0040, blueprint Partie XV): ``raw_market_events`` / ``instrument_master`` /
``daily_bar`` are the durable truth, and every non-raw partition is reconstructable from them. When
a derived contract evolves, old partitions go non-conforming (the 2026-06-15 incident:
``projected_option_analytics`` lacked the now-required ``surface_side``); the operator primitive for
that is *purge the derived layer for the day and rebuild it from raw* -- deterministically, with no
broker round-trip.

This script is the guarded operator wrapper around the existing reconstruction path
(``algotrading.infra.orchestration.reconstruction.reconstruct_day``). It adds no reconstruction
logic. Per day it:

* refuses to do anything unless the raw partition is present (the mandatory guard -- never purge
  what cannot be rebuilt);
* resolves the original close ``as_of`` from the existing snapshot layer (or ``--as-of``), so the
  rebuilt rows reproduce the original provenance stamps;
* backs up, then purges, the non-raw partitions that ``reconstruct_day`` owns -- the snapshot,
  derived, and projected-analytics tables it writes via ``persist_outputs``;
* replays the stored raw through ``reconstruct_day`` (``persist=True``), then hash-verifies that the
  ``raw/`` layer is byte-for-byte untouched.

It never re-hits a broker and never writes to ``raw/``. It is index-options-only and operates at the
whole-day granularity ``reconstruct_day`` does (one ``as_of`` per day); ``--index`` scopes only the
raw-presence assertion. Reference, portfolio, QC, and signal partitions are inputs or are not
produced by ``reconstruct_day``, so they are left untouched.

Usage:
    uv run python scripts/rebuild_from_raw.py --trade-date 2026-06-12 --index SX5E
    uv run python scripts/rebuild_from_raw.py --start 2026-06-10 --end 2026-06-12 --dry-run
    uv run python scripts/rebuild_from_raw.py --trade-date 2026-06-12 --as-of 2026-06-12T16:30+00:00
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import structlog
from algotrading.core.config import config_hashes, load_platform_config
from algotrading.core.paths import data_root, repo_root
from algotrading.infra.orchestration.reconstruction import reconstruct_day
from algotrading.infra.storage import ParquetStore
from algotrading.infra.storage.partitioning import table_dir

_LOGGER = structlog.get_logger("scripts.rebuild_from_raw")

RAW_LAYER = "raw"
RAW_EVENTS_TABLE = "raw_market_events"
SNAPSHOTS_TABLE = "market_state_snapshots"
INSTRUMENT_MASTER_TABLE = "instrument_master"
POSITIONS_TABLE = "positions"
BACKUP_DIR_NAME = "_rebuild_backups"

REBUILT_TABLES: tuple[str, ...] = (
    "market_state_snapshots",
    "forward_curve",
    "iv_points",
    "surface_parameters",
    "surface_grid",
    "pricing_results",
    "risk_aggregates",
    "scenario_results",
    "projected_option_analytics",
)


class RebuildError(Exception):
    pass


class RawAbsentError(RebuildError):
    def __init__(self, trade_date: date, index: str | None) -> None:
        scope = f"index {index}" if index is not None else "any index"
        super().__init__(
            f"raw partition absent for {trade_date.isoformat()} ({scope}); "
            "refusing to purge a derived layer that cannot be rebuilt"
        )
        self.trade_date = trade_date
        self.index = index


class AsOfUnresolvedError(RebuildError):
    def __init__(self, trade_date: date, distinct_count: int) -> None:
        super().__init__(
            f"cannot resolve a single as_of for {trade_date.isoformat()} "
            f"({distinct_count} distinct snapshot timestamps in the snapshot layer); "
            "pass --as-of explicitly"
        )
        self.trade_date = trade_date
        self.distinct_count = distinct_count


class BackupExistsError(RebuildError):
    def __init__(self, backup_dir: Path) -> None:
        super().__init__(f"backup target already exists, refusing to overwrite: {backup_dir}")
        self.backup_dir = backup_dir


class RawMutatedError(RebuildError):
    def __init__(self, trade_date: date) -> None:
        super().__init__(
            f"raw layer hash changed during rebuild of {trade_date.isoformat()} -- "
            "the raw keystone was mutated, which must never happen"
        )
        self.trade_date = trade_date


@dataclass(frozen=True, slots=True)
class RebuildResult:
    trade_date: date
    status: str
    record_count: int
    purged_dirs: tuple[Path, ...]
    backup_dir: Path | None
    raw_hash: str
    dry_run: bool


def raw_present(store: ParquetStore, trade_date: date, index: str | None = None) -> bool:
    partitions = store.list_partitions(RAW_EVENTS_TABLE)
    if index is not None:
        return (trade_date, index) in partitions
    return any(partition_date == trade_date for partition_date, _underlying in partitions)


def resolve_as_of(
    store: ParquetStore, trade_date: date, override: datetime | None = None
) -> datetime:
    if override is not None:
        return override
    snapshots = store.read(SNAPSHOTS_TABLE, trade_date=trade_date)
    stamps = sorted({snapshot.snapshot_ts for snapshot in snapshots})
    if len(stamps) == 1:
        return stamps[0]
    raise AsOfUnresolvedError(trade_date, len(stamps))


def rebuilt_partition_dirs(root: Path, trade_date: date) -> list[Path]:
    segment = f"trade_date={trade_date.isoformat()}"
    dirs: list[Path] = []
    for table in REBUILT_TABLES:
        base = table_dir(root, table)
        if not base.exists():
            continue
        dirs.extend(sorted(path for path in base.glob(f"**/{segment}") if path.is_dir()))
    return dirs


def hash_tree(path: Path) -> str:
    digest = hashlib.sha256()
    if not path.exists():
        return digest.hexdigest()
    for file in sorted(path.rglob("*")):
        if file.is_file():
            digest.update(str(file.relative_to(path)).encode())
            digest.update(file.read_bytes())
    return digest.hexdigest()


def backup_partition_dirs(dirs: list[Path], root: Path, backup_dir: Path) -> None:
    if backup_dir.exists():
        raise BackupExistsError(backup_dir)
    for source in dirs:
        destination = backup_dir / source.relative_to(root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination)


def purge_partition_dirs(dirs: list[Path]) -> None:
    for source in dirs:
        if source.exists():
            shutil.rmtree(source)


def rebuild_day(
    store: ParquetStore,
    trade_date: date,
    *,
    configs_dir: Path,
    index: str | None = None,
    as_of_override: datetime | None = None,
    backup_root: Path | None = None,
    dry_run: bool = False,
    correlation_id: str = "rebuild-from-raw",
) -> RebuildResult:
    log = _LOGGER.bind(trade_date=trade_date.isoformat(), index=index or "", dry_run=dry_run)
    root = store.root
    if not raw_present(store, trade_date, index):
        raise RawAbsentError(trade_date, index)

    raw_layer = root / RAW_LAYER
    as_of = resolve_as_of(store, trade_date, as_of_override)
    purge_dirs = rebuilt_partition_dirs(root, trade_date)
    raw_hash_before = hash_tree(raw_layer)

    if dry_run:
        log.info("rebuild.dry_run", purge_dir_count=len(purge_dirs), as_of=as_of.isoformat())
        return RebuildResult(
            trade_date=trade_date,
            status="DRY_RUN",
            record_count=0,
            purged_dirs=tuple(purge_dirs),
            backup_dir=None,
            raw_hash=raw_hash_before,
            dry_run=True,
        )

    backup_dir: Path | None = None
    if backup_root is not None and purge_dirs:
        backup_dir = backup_root / trade_date.isoformat()
        backup_partition_dirs(purge_dirs, root, backup_dir)
    purge_partition_dirs(purge_dirs)

    masters = store.read(INSTRUMENT_MASTER_TABLE)
    instruments = [master.instrument for master in masters]
    positions = store.read(POSITIONS_TABLE, trade_date=trade_date)
    config = load_platform_config(configs_dir)
    hashes = config_hashes(config)

    reconstruction = reconstruct_day(
        store,
        trade_date,
        positions,
        instruments=instruments,
        masters=masters,
        config=config,
        config_hashes=hashes,
        as_of=as_of,
        calc_ts=as_of,
        persist=True,
        correlation_id=correlation_id,
    )

    raw_hash_after = hash_tree(raw_layer)
    if raw_hash_after != raw_hash_before:
        raise RawMutatedError(trade_date)

    log.info(
        "rebuild.day.done",
        status=reconstruction.status,
        record_count=reconstruction.record_count,
        purge_dir_count=len(purge_dirs),
        backup_dir=str(backup_dir) if backup_dir is not None else "",
    )
    return RebuildResult(
        trade_date=trade_date,
        status=reconstruction.status,
        record_count=reconstruction.record_count,
        purged_dirs=tuple(purge_dirs),
        backup_dir=backup_dir,
        raw_hash=raw_hash_after,
        dry_run=False,
    )


def date_range(start: date, end: date) -> tuple[date, ...]:
    if end < start:
        raise RebuildError(f"end {end.isoformat()} precedes start {start.isoformat()}")
    span = (end - start).days
    return tuple(start + timedelta(days=offset) for offset in range(span + 1))


def rebuild_range(
    store: ParquetStore,
    start: date,
    end: date,
    *,
    configs_dir: Path,
    index: str | None = None,
    as_of_override: datetime | None = None,
    backup_root: Path | None = None,
    dry_run: bool = False,
) -> list[RebuildResult]:
    return [
        rebuild_day(
            store,
            trade_date,
            configs_dir=configs_dir,
            index=index,
            as_of_override=as_of_override,
            backup_root=backup_root,
            dry_run=dry_run,
        )
        for trade_date in date_range(start, end)
    ]


def _as_aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--trade-date", type=date.fromisoformat, help="single ISO trade date")
    group.add_argument("--start", type=date.fromisoformat, help="ISO range start (with --end)")
    parser.add_argument("--end", type=date.fromisoformat, help="ISO range end (with --start)")
    parser.add_argument("--index", default=None, help="index symbol scoping the raw-presence guard")
    parser.add_argument(
        "--as-of",
        type=lambda value: _as_aware(datetime.fromisoformat(value)),
        default=None,
        help="override the close timestamp (default: derived from the snapshot layer)",
    )
    parser.add_argument(
        "--data-root", type=Path, default=data_root(), help="store root (default: the data_root)"
    )
    parser.add_argument(
        "--configs", type=Path, default=repo_root() / "configs", help="config bundle directory"
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=None,
        help="backup root (default: <data-root>/_rebuild_backups)",
    )
    parser.add_argument(
        "--no-backup", action="store_true", help="skip backing up purged partitions"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="report what would be purged, touch nothing"
    )
    return parser.parse_args(argv)


def _print_result(result: RebuildResult) -> None:
    if result.dry_run:
        print(
            f"[DRY_RUN] {result.trade_date.isoformat()} -- "
            f"would purge {len(result.purged_dirs)} partition dir(s)"
        )
        for path in result.purged_dirs:
            print(f"    {path}")
        return
    backup = f", backup -> {result.backup_dir}" if result.backup_dir is not None else ""
    print(
        f"[{result.status}] {result.trade_date.isoformat()} -- "
        f"{result.record_count} record(s) rebuilt, "
        f"{len(result.purged_dirs)} partition dir(s) purged{backup}"
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if (args.start is None) != (args.end is None):
        print("error: --start and --end must be given together", file=sys.stderr)
        return 1

    store = ParquetStore(args.data_root)
    backup_root: Path | None = None
    if not args.no_backup:
        backup_root = (
            args.backup_dir if args.backup_dir is not None else args.data_root / BACKUP_DIR_NAME
        )

    try:
        if args.trade_date is not None:
            results = [
                rebuild_day(
                    store,
                    args.trade_date,
                    configs_dir=args.configs,
                    index=args.index,
                    as_of_override=args.as_of,
                    backup_root=backup_root,
                    dry_run=args.dry_run,
                )
            ]
        else:
            results = rebuild_range(
                store,
                args.start,
                args.end,
                configs_dir=args.configs,
                index=args.index,
                as_of_override=args.as_of,
                backup_root=backup_root,
                dry_run=args.dry_run,
            )
    except RebuildError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    for result in results:
        _print_result(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
