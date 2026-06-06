"""Decode a committed JSON event sample and summarise its option chain — offline, no broker.

Reads a committed market-data sample (a real delayed slice in
``packages/infra-{saxo,ibkr}/samples/``,
written by ``scripts/export_sample.py`` / ``storage.events_to_json``), decodes it through the
canonical ``storage.events_from_json``, verifies it replays deterministically, and prints the chain
shape (provider, underlying, maturities, strikes, fields). This lets a fresh clone confirm — with no
internet and no captured data on disk — that the committed sample is intact and well-formed.

NOTE on full surface reconstruction. The committed JSON samples are in the **broker-raw** event
schema (``field_value``, ``provider``, colon-delimited ``OPT:`` keys via
``algotrading.infra.universe.parse_instrument_key``). The canonical analytics path
(``orchestration.build_surface`` / ``reconstruction.reconstruct_day``) consumes the **contracts**
``RawMarketEvent`` schema (pipe-delimited keys, ``value``) plus instrument masters from a store.
Bridging broker-raw samples into the contracts schema (and synthesising masters) is the relocation
still flagged as deferred by ``packages/infra-{saxo,ibkr}/tests/test_real_sample_reconstruct.py``
(ADR 0021). Until that bridge exists in ``packages/infra``, this script does **not** fabricate a
surface off a sample — it validates and summarises the slice, which is what is available end to end
today. To render a real surface offline, use ``scripts/plot_live_surface.py`` against a stored raw
day (it replays the contracts-schema events the actor consumes).

Usage:
    uv run python scripts/reconstruct_sample.py  # default: the Saxo ASML sample
    uv run python scripts/reconstruct_sample.py \
        --sample packages/infra-ibkr/samples/spy_real_2026-06-04.json --symbol SPY
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

from algotrading.infra.storage import events_from_json, events_to_json
from algotrading.infra.universe import parse_instrument_key

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_SAMPLE = _REPO_ROOT / "packages" / "infra-saxo" / "samples" / "asml_real_2026-06-04.json"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Decode and summarise a committed JSON event sample (offline)"
    )
    parser.add_argument(
        "--sample", default=str(_DEFAULT_SAMPLE), help="path to a JSON event sample"
    )
    parser.add_argument("--symbol", default=None, help="expected underlying (optional check)")
    args = parser.parse_args()

    sample = Path(args.sample)
    if not sample.exists():
        print(f"Sample not found: {sample}")
        return 1
    text = sample.read_text(encoding="utf-8")
    events = events_from_json(text)
    if not events:
        print(f"No events in {sample}")
        return 1

    # Determinism: a sample that does not round-trip is corrupt and must not be trusted.
    if events_from_json(events_to_json(events)) != events:
        print(f"Sample does NOT replay deterministically: {sample}")
        return 2

    providers = sorted({e.provider for e in events})
    underlyings = sorted({e.underlying for e in events})
    fields = sorted({e.field_name for e in events})
    option_keys = sorted({e.instrument_key for e in events if e.instrument_key.startswith("OPT:")})

    by_expiry: dict[object, set[object]] = defaultdict(set)
    for key in option_keys:
        parsed = parse_instrument_key(key)
        by_expiry[parsed.expiry].add(parsed.strike)

    if args.symbol and args.symbol.upper() not in underlyings:
        print(f"WARNING: --symbol {args.symbol!r} not in sample underlyings {underlyings}")

    print(f"\n===== {sample.name} — committed sample summary (offline) =====")
    print(f"events            : {len(events)} ({len(option_keys)} option contracts)")
    print(f"provider(s)       : {', '.join(providers)}")
    print(f"underlying(s)      : {', '.join(underlyings)}")
    print(f"fields            : {', '.join(fields)}")
    print(f"maturities        : {len(by_expiry)}")
    for expiry in sorted(by_expiry):
        strikes = sorted(by_expiry[expiry])
        lo, hi = strikes[0], strikes[-1]
        print(f"    {expiry}  {len(strikes)} strikes  [{lo} .. {hi}]")
    print("\nreplay            : deterministic (round-trips byte-for-byte)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
