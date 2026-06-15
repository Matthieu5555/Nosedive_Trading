"""One-shot daily_bar cold-compaction (ADR 0034 §3 / infra-daily-bar-compaction spec).

Converts the ~228k one-row-per-file daily_bar hot layout into one Parquet file per
ticker with ``trade_date`` as a sorted column — from ~228k files → ~50, 2.7 GB → ~30 MB,
with date-range reads pruning by DuckDB row-group min/max stats instead of opening
thousands of footers.

The compaction is **value-preserving and idempotent**:

1. For each (provider, ticker), collect all hot per-day parquet files.
2. Read all rows via DuckDB, sort by trade_date, deduplicate on (provider, underlying,
   trade_date) — the primary key — so a re-run is clean.
3. Verify row count via parquet metadata (cheap, no column reads).
4. Write the cold file atomically (stage to temp, rename).
5. Archive the superseded hot files to ``data/_archive/daily_bar/`` and remove them
   from the live tree once the cold file is verified.

The canonical ``data/`` store is never touched until **after** row-identity verification
passes.  A ``--dry-run`` flag prints the plan without changing any files.

Usage::

    # Compact all tickers for all providers (default):
    uv run python scripts/compact_daily_bar.py

    # Compact a single ticker (e.g. for testing / incremental roll-out):
    uv run python scripts/compact_daily_bar.py --underlying SX5E

    # Compact a specific provider:
    uv run python scripts/compact_daily_bar.py --provider IBKR

    # Dry-run: show what would be done without writing:
    uv run python scripts/compact_daily_bar.py --dry-run

    # Re-compact even tickers that already have a cold file (force overwrite):
    uv run python scripts/compact_daily_bar.py --force

    # Archive hot files to a non-default location:
    uv run python scripts/compact_daily_bar.py --archive-root /tmp/daily_bar_archive

Safety:
    The canonical data/ store is read and written in-place by this script.
    NEVER run this against a store you are not prepared to commit to.
    Run ``scripts/backup_data_store.py backup`` first.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path

from algotrading.core.paths import data_root

# Import after path setup — these live in the editable workspace.
from algotrading.infra.storage.compaction import (
    compact_ticker,
    compacted_file_path,
    list_hot_files_for_ticker,
)
from algotrading.infra.storage.partitioning import table_dir

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TABLE = "daily_bar"
_DEFAULT_ARCHIVE_SUBDIR = "_archive/daily_bar"


# ---------------------------------------------------------------------------
# Discovery helpers
# ---------------------------------------------------------------------------


def discover_tickers(
    store_root: Path,
    provider: str | None,
) -> list[tuple[str, str]]:
    """Return all (provider, underlying) pairs present in the hot layout.

    Walks ``raw/daily_bar/provider=<P>/trade_date=<D>/underlying=<SYM>/`` and returns
    the de-duplicated set of (provider, ticker) pairs.  When ``provider`` is given,
    only that provider's segment is scanned.
    """
    base = table_dir(store_root, _TABLE)
    if not base.exists():
        return []

    provider_dirs: list[Path]
    if provider is not None:
        p = base / f"provider={provider}"
        provider_dirs = [p] if p.exists() else []
    else:
        provider_dirs = [
            p for p in base.iterdir() if p.is_dir() and p.name.startswith("provider=")
        ]

    seen: set[tuple[str, str]] = set()
    pairs: list[tuple[str, str]] = []
    for prov_dir in sorted(provider_dirs):
        prov_name = prov_dir.name.split("=", 1)[1]
        for date_dir in prov_dir.iterdir():
            if not date_dir.is_dir() or not date_dir.name.startswith("trade_date="):
                continue
            for und_dir in date_dir.iterdir():
                if not und_dir.is_dir() or not und_dir.name.startswith("underlying="):
                    continue
                ticker = und_dir.name.split("=", 1)[1]
                key = (prov_name, ticker)
                if key not in seen:
                    seen.add(key)
                    pairs.append(key)
    return sorted(pairs)


def _archive_hot_files(
    hot_files: list[Path],
    archive_root: Path,
    *,
    dry_run: bool = False,
) -> None:
    """Move hot partition files to the archive directory tree.

    The archive mirrors the original hot layout so recovery is a directory copy.
    The hot partition directory (``underlying=SYM/``) is moved whole; the date
    directory is pruned when it becomes empty.
    """
    for f in hot_files:
        partition_dir = f.parent   # underlying=SYM/
        date_dir = partition_dir.parent  # trade_date=D/
        provider_dir = date_dir.parent   # provider=P/

        # Build the archive path mirroring the source structure:
        # raw/daily_bar/provider=P/trade_date=D/underlying=SYM
        rel = partition_dir.relative_to(provider_dir.parent.parent)
        dest = archive_root / rel

        if dry_run:
            print(f"  [DRY-RUN] would archive {partition_dir} → {dest}")
            continue

        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copytree(partition_dir, dest, dirs_exist_ok=True)
            shutil.rmtree(partition_dir)
            # Prune the date directory if now empty.
            if date_dir.exists() and not any(date_dir.iterdir()):
                date_dir.rmdir()
        except OSError as exc:
            log.warning("archive_hot_files: failed for %s: %s", f, exc)


# ---------------------------------------------------------------------------
# Main compaction loop
# ---------------------------------------------------------------------------


def compact_all(
    store_root: Path,
    archive_root: Path,
    *,
    provider: str | None = None,
    underlying: str | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> int:
    """Compact all matching (provider, ticker) pairs.  Returns exit code (0 = success)."""
    if underlying is not None:
        # Single-ticker mode: discover which providers have it.
        base = table_dir(store_root, _TABLE)
        if not base.exists():
            log.error("daily_bar table directory not found at %s", base)
            return 1
        prov_dirs = (
            [base / f"provider={provider}"]
            if provider is not None
            else [p for p in base.iterdir() if p.is_dir() and p.name.startswith("provider=")]
        )
        pairs: list[tuple[str, str]] = []
        for pd in sorted(prov_dirs):
            prov_name = pd.name.split("=", 1)[1]
            # A ticker is present when at least one hot file exists.
            hot = list_hot_files_for_ticker(store_root, _TABLE, prov_name, underlying)
            if hot:
                pairs.append((prov_name, underlying))
        if not pairs:
            # Also check if a cold file exists (already compacted but no hot files).
            for pd in sorted(prov_dirs):
                prov = pd.name.split("=", 1)[1]
                cold = compacted_file_path(store_root, _TABLE, prov, underlying)
                if cold.exists():
                    pairs.append((prov, underlying))
        if not pairs:
            log.warning("No data found for underlying=%s", underlying)
            return 0
    else:
        pairs = discover_tickers(store_root, provider)

    if not pairs:
        log.info("No hot-layout tickers found — nothing to compact.")
        return 0

    log.info("Compacting %d ticker(s) ...", len(pairs))
    errors = 0
    skipped = 0
    compacted = 0

    for prov, ticker in pairs:
        cold = compacted_file_path(store_root, _TABLE, prov, ticker)
        hot_files = list_hot_files_for_ticker(store_root, _TABLE, prov, ticker)

        if cold.exists() and not hot_files and not force:
            # Already fully compacted and no new hot files to merge in.
            log.debug("skip %s/%s — already compacted, no hot files", prov, ticker)
            skipped += 1
            continue
        # When cold exists AND there are hot files: merge both into an updated cold file.

        if dry_run:
            print(
                f"[DRY-RUN] compact {prov}/{ticker}: "
                f"{len(hot_files)} hot file(s), cold={'exists' if cold.exists() else 'absent'}"
            )
            skipped += 1
            continue

        try:
            compact_ticker(
                store_root,
                prov,
                ticker,
                table=_TABLE,
                remove_hot=False,  # Archive separately below.
            )
            # Archive then remove the hot files that are now superseded.
            if hot_files:
                _archive_hot_files(hot_files, archive_root, dry_run=False)
            compacted += 1
            log.info("compacted %s/%s (%d hot files)", prov, ticker, len(hot_files))
        except Exception as exc:
            log.error("FAILED %s/%s: %s", prov, ticker, exc, exc_info=True)
            errors += 1

    print(
        f"\nSummary: {compacted} compacted, {skipped} skipped, {errors} errors "
        f"(total {len(pairs)} ticker-provider pairs)."
    )
    return 1 if errors else 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compact daily_bar hot files into one file per ticker (ADR 0034 §3).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--underlying",
        metavar="SYM",
        help="Compact only this ticker (all providers unless --provider is also given).",
    )
    parser.add_argument(
        "--provider",
        metavar="P",
        help="Compact only this provider's segment (default: all providers).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-compact even tickers that already have a cold file.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the plan without changing any files.",
    )
    parser.add_argument(
        "--data-root",
        metavar="DIR",
        help="Store root (default: $ALGOTRADING_DATA_ROOT or <repo>/data).",
    )
    parser.add_argument(
        "--archive-root",
        metavar="DIR",
        help=(
            "Where to move the superseded hot files "
            "(default: <data-root>/_archive/daily_bar)."
        ),
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable DEBUG-level logging.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
    )

    store_root = Path(args.data_root) if args.data_root else data_root()
    archive_root = (
        Path(args.archive_root) if args.archive_root else store_root / _DEFAULT_ARCHIVE_SUBDIR
    )

    log.info("Store root   : %s", store_root)
    log.info("Archive root : %s", archive_root)
    if args.dry_run:
        log.info("DRY-RUN mode — no files will be modified.")

    return compact_all(
        store_root,
        archive_root,
        provider=args.provider,
        underlying=args.underlying,
        force=args.force,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    sys.exit(main())
