"""WS 1A — one-shot index-membership ingest.

Pulls an index's constituents from a free third-party source and writes them, bitemporal and
append-only, into the ``index_constituents`` reference table (the resolver/contract/write all
already live in ``algotrading.infra.universe``; this shim only picks the source and runs it).
Membership is **not** an IBKR feed (verified against the official IBKR docs) and **not** Yahoo
(owner mandate OQ-2) — see ``universe/membership_source.py``.

Usage:
    uv run python scripts/ingest_membership.py --index SPX
    uv run python scripts/ingest_membership.py --index SPX --store-root /srv/project/data
    uv run python scripts/ingest_membership.py --index SX5E --knowledge-date 2026-06-07
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from algotrading.infra.storage import ParquetStore
from algotrading.infra.universe import (
    MembershipSource,
    SP500DatasetsSource,
    YfiuaSnapshotSource,
    ingest_membership_changes,
)

# Which source serves which index. SPX has a free dated source (real add dates); SX5E falls back
# to the yfiua current snapshot (no dated history yet — Siblis is the OQ-3 upgrade).
_SOURCES: dict[str, MembershipSource] = {
    "SPX": SP500DatasetsSource(),
    "SX5E": YfiuaSnapshotSource(code="sx5e", default_add_date=date(2000, 1, 1)),
}


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest index membership from a third party.")
    parser.add_argument("--index", required=True, help="Index symbol, e.g. SPX or SX5E.")
    parser.add_argument(
        "--store-root",
        type=Path,
        default=Path("data"),
        help="ParquetStore root (default: ./data).",
    )
    parser.add_argument(
        "--knowledge-date",
        type=date.fromisoformat,
        default=None,
        help="Knowledge axis date (ISO). Default: today (UTC).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    index = args.index.upper()
    source = _SOURCES.get(index)
    if source is None:
        print(f"[FAIL] no membership source configured for index {index!r}", file=sys.stderr)
        return 2

    knowledge_date = args.knowledge_date or datetime.now(UTC).date()
    print(f"[..] fetching {index} membership (knowledge_date={knowledge_date.isoformat()})")
    changes = source.fetch(index, knowledge_date)
    if not changes:
        print(f"[FAIL] source returned no constituents for {index}", file=sys.stderr)
        return 1

    store = ParquetStore(args.store_root)
    written = ingest_membership_changes(store, changes)
    print(
        f"[OK] {index}: {len(changes)} constituents fetched, "
        f"{len(written)} rows resolved into {args.store_root}/ (index_constituents)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
