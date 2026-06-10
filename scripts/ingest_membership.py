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
    CsvFileSource,
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
    parser.add_argument(
        "--csv",
        type=Path,
        default=None,
        help=(
            "Path to a committed members CSV (overrides the built-in source). Use this to seed "
            "weights the free feed lacks — the file needs a symbol column and, for weights, a "
            "weight column (see --symbol-field / --weight-field)."
        ),
    )
    parser.add_argument(
        "--vendor",
        default=None,
        help="Provenance label for a --csv source (e.g. 'spdr-spy-holdings-2026-06-10').",
    )
    parser.add_argument("--symbol-field", default="Symbol", help="--csv symbol column name.")
    parser.add_argument(
        "--weight-field",
        default="Weight",
        help="--csv weight column name (blank cells stay None, never zeroed).",
    )
    parser.add_argument(
        "--add-date-field",
        default=None,
        help="--csv add-date column name, if the file carries real per-name add dates.",
    )
    parser.add_argument(
        "--default-add-date",
        type=date.fromisoformat,
        default=date(2000, 1, 1),
        help="Fallback effective_add_date for --csv rows without an add-date column.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    index = args.index.upper()
    source: MembershipSource | None
    if args.csv is not None:
        if args.vendor is None:
            print("[FAIL] --csv requires --vendor (provenance label)", file=sys.stderr)
            return 2
        source = CsvFileSource(
            path=args.csv,
            vendor=args.vendor,
            default_add_date=args.default_add_date,
            symbol_field=args.symbol_field,
            add_date_field=args.add_date_field,
            weight_field=args.weight_field,
        )
    else:
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
