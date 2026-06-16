"""One-time migration: relocate legacy flat partitions under ``run=_legacy/``.

Run-partitioning (registry ``run_partitioned=True``) inserts a ``run=<run_id>`` segment between
``trade_date`` and ``underlying`` so each fetch keeps its own dataset. Stores written before that
change have the flat ``trade_date/underlying`` layout. The read path keeps such flat files visible
(``run_dir is None`` is always kept), which means once a NEW run-partitioned fetch lands for a date
that also has flat data, a default read unions BOTH — double-counting the underlying.

This migration removes that ambiguity by moving every existing flat partition into a synthetic
``run=_legacy`` directory, so the whole store is uniformly run-partitioned and the newest-run rule
governs cleanly. It is idempotent (a trade_date already carrying ``run=`` dirs is skipped) and
reversible (it only moves directories). Default is a dry run; pass ``--apply`` to move.

    uv run python scripts/migrate_run_partition.py            # show the plan
    uv run python scripts/migrate_run_partition.py --apply    # perform the moves
"""

from __future__ import annotations

import argparse
from pathlib import Path

from algotrading.core.paths import data_root
from algotrading.infra.contracts.registry import REGISTRY
from algotrading.infra.storage.partitioning import ADHOC_RUN, table_dir

LEGACY_RUN = "_legacy"


def _date_roots(base: Path, provider_partitioned: bool) -> list[Path]:
    if not provider_partitioned:
        return [base] if base.exists() else []
    if not base.exists():
        return []
    return [p for p in sorted(base.iterdir()) if p.is_dir() and p.name.startswith("provider=")]


def plan_moves(root: Path) -> list[tuple[Path, Path]]:
    moves: list[tuple[Path, Path]] = []
    for name, spec in REGISTRY.items():
        if not getattr(spec, "run_partitioned", False):
            continue
        base = table_dir(root, name)
        for date_root in _date_roots(base, spec.provider_partitioned):
            for td_dir in sorted(date_root.glob("trade_date=*")):
                flat = [
                    p
                    for p in sorted(td_dir.iterdir())
                    if p.is_dir() and p.name.startswith("underlying=")
                ]
                if not flat:
                    continue  # already run-partitioned (or empty) — idempotent skip
                legacy_dir = td_dir / f"run={LEGACY_RUN}"
                for underlying_dir in flat:
                    moves.append((underlying_dir, legacy_dir / underlying_dir.name))
    return moves


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="perform the moves (default: dry run)")
    args = parser.parse_args()

    root = data_root()
    moves = plan_moves(root)

    if not moves:
        print(f"nothing to migrate under {root} — store is already run-partitioned")
        return

    assert LEGACY_RUN != ADHOC_RUN, "legacy and adhoc run segments must stay distinct"
    print(f"{'APPLY' if args.apply else 'DRY RUN'}: {len(moves)} partition(s) under {root}")
    for src, dest in moves:
        print(f"  {src.relative_to(root)}  ->  {dest.relative_to(root)}")
        if args.apply:
            dest.parent.mkdir(parents=True, exist_ok=True)
            src.rename(dest)
    if not args.apply:
        print("\nre-run with --apply to perform these moves")


if __name__ == "__main__":
    main()
