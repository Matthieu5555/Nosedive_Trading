"""Export a curated, committable JSON sample from a captured raw day.

The snapshot uses only the latest tick per (instrument, field), so keeping just those reproduces
the exact same surface from a tiny file — a public delayed-quote slice for offline notebook repro.
This script computes that curated last-tick set off a stored raw day.

KNOWN GAP — does not write a sample yet. The committed JSON sample format
(``storage.events_to_json`` / ``events_from_json``, the shape under
``packages/infra-{saxo,ibkr}/samples/``) is the **broker-raw** ``RawMarketEvent`` schema:
``collector_session_id`` / ``field_value`` / ``provider`` and colon-delimited ``OPT:`` instrument
keys (``algotrading.infra.universe.parse_instrument_key``). A day read back from the canonical store
via ``collectors.replay_day`` is the **contracts**
``RawMarketEvent`` schema: ``session_id`` / ``value`` / ``underlying`` and pipe-delimited keys. The
two are different classes with different key formats; serializing the store day through
``events_to_json`` is therefore not possible without a translation layer that does not exist in
``packages/infra`` today (the same broker-raw ↔ contracts bridge deferred under ADR 0021, see
``packages/infra-{saxo,ibkr}/tests/test_real_sample_reconstruct.py``). Rather than emit a malformed
sample, this script computes and reports the curated last-tick set and then explains the gap. Once
the bridge lands in ``packages/infra``, wire the curated set through it into ``events_to_json``.

Usage:
    uv run python scripts/export_sample.py --symbol AAPL --date 2026-05-29 --out path/to/sample.json
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from algotrading.infra.collectors import replay_day
from algotrading.infra.storage import ParquetStore

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DATA_ROOT = _REPO_ROOT / "data"


def _latest_day(store: ParquetStore, symbol: str) -> date | None:
    days = [d for d, u in store.list_partitions("raw_market_events") if u == symbol]
    return max(days) if days else None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compute a curated last-tick set from a stored day"
    )
    parser.add_argument("--symbol", default="AAPL")
    parser.add_argument(
        "--date", default=None, help="trade date YYYY-MM-DD (default: latest stored day)"
    )
    parser.add_argument(
        "--store-root", default=None, help=f"raw store root (default: {_DATA_ROOT})"
    )
    parser.add_argument("--out", required=True, help="intended output JSON sample path")
    args = parser.parse_args()

    symbol = args.symbol.upper()
    store = ParquetStore(Path(args.store_root) if args.store_root else _DATA_ROOT)
    day = date.fromisoformat(args.date) if args.date else _latest_day(store, symbol)
    if day is None:
        print(f"No stored day for {symbol} in {store!r}.")
        return 1

    events = replay_day(store, day, underlying=symbol)
    if not events:
        print(f"No events for {symbol} on {day}.")
        return 1

    # Keep only the latest tick per (instrument, field) — exactly what the snapshot consumes.
    last: dict[tuple[str, str], object] = {}
    for e in events:
        key = (e.instrument_key, e.field_name)
        prev = last.get(key)
        if prev is None or e.receipt_ts > prev.receipt_ts:  # type: ignore[attr-defined]
            last[key] = e
    curated = sorted(
        last.values(),
        key=lambda e: (e.instrument_key, e.field_name),  # type: ignore[attr-defined]
    )

    print(
        f"curated {len(curated)} last-tick events from {len(events)} captured "
        f"({symbol} {day}); intended out: {args.out}"
    )
    print(
        "NOT WRITTEN: the store uses the contracts RawMarketEvent schema (pipe-delimited keys, "
        "`value`), but the committed sample format expects the broker-raw schema (`OPT:` keys, "
        "`field_value`). Bridging them is deferred under ADR 0021 — see this script's module "
        "docstring and scripts/README.md."
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
