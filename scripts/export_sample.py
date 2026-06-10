"""Export a curated, committable JSON sample from a captured raw day.

The snapshot uses only the latest tick per (instrument, field), so keeping just those reproduces
the exact same surface from a tiny file — a public delayed-quote slice for offline notebook repro.
This script computes that curated last-tick set off a stored raw day and writes it as a committable
broker-raw JSON sample (the shape under ``packages/infra-{saxo,ibkr}/samples/``).

A stored day read via ``collectors.replay_day`` is the **contracts** ``RawMarketEvent`` schema
(``session_id`` / ``value`` / pipe keys); the committed sample format is the **broker-raw** schema
(``collector_session_id`` / ``field_value`` / colon ``OPT:`` keys / ``provider``). The conversion
is the one bridge in ``universe.sample_bridge`` (``contracts_to_events``, ADR 0039) — ``provider``
is re-supplied here (OQ-A) and ``field_value`` is exact to the stored float precision (OQ-B). Round-
trip the result with ``scripts/reconstruct_sample.py`` to confirm it replays deterministically.

Usage:
    uv run python scripts/export_sample.py --symbol SX5E --date 2026-06-10 \
        --provider IBKR --out packages/infra-ibkr/samples/sx5e_real_2026-06-10.json
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from algotrading.infra.collectors import replay_day
from algotrading.infra.contracts import RawMarketEvent
from algotrading.infra.storage import ParquetStore, events_to_json
from algotrading.infra.universe import contracts_to_events

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DATA_ROOT = _REPO_ROOT / "data"


def _latest_day(store: ParquetStore, symbol: str) -> date | None:
    days = [d for d, u in store.list_partitions("raw_market_events") if u == symbol]
    return max(days) if days else None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export a curated last-tick broker-raw sample from a stored raw day"
    )
    parser.add_argument("--symbol", default="AAPL")
    parser.add_argument(
        "--date", default=None, help="trade date YYYY-MM-DD (default: latest stored day)"
    )
    parser.add_argument(
        "--provider", default="IBKR", help="source label stamped on the sample (OQ-A)"
    )
    parser.add_argument(
        "--store-root", default=None, help=f"raw store root (default: {_DATA_ROOT})"
    )
    parser.add_argument("--out", required=True, help="output JSON sample path")
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
    # canonical_ts is the as-of ordering field, so it picks the same tick the analytics would.
    last: dict[tuple[str, str], RawMarketEvent] = {}
    for event in events:
        key = (event.instrument_key, event.field_name)
        prev = last.get(key)
        if prev is None or event.canonical_ts > prev.canonical_ts:
            last[key] = event
    curated = sorted(last.values(), key=lambda e: (e.instrument_key, e.field_name))

    # The one bridge: contracts (stored) → broker-raw (sample wire-format), provider re-supplied.
    broker_events = contracts_to_events(curated, provider=args.provider)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(events_to_json(broker_events), encoding="utf-8")

    print(
        f"wrote {len(broker_events)} curated last-tick events "
        f"(from {len(events)} captured, {symbol} {day}, provider={args.provider}) to {out_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
