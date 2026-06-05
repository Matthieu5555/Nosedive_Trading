#!/usr/bin/env python
"""Produce one SAMPLE trade date of real pipeline output into a shared data root.

A throwaway convergence tool (the C4 slice): it exists so the operator frontend can
read genuine pipeline output before a live capture lands, and it dies with C5 when
the ``backend/`` tree is retired. It replays the committed ``synthetic_known_answer``
chain through the same public orchestration entry the tests pin
(:func:`orchestration.run_incremental_analytics`), with a small fixture book over the
chain's real contract keys — so every derived row in the store (snapshots, forwards,
IV points, SVI surface, pricing results, risk aggregates, scenario PnL) comes out of
the real analytics pipeline, not hand-synthesized values. Only the *input quotes* are
synthetic; the retreatment is the production code path.

The raw layer is append-only: re-running against a root that already holds the
fixture's trade date skips the seeding and just re-runs the (idempotent,
replace-semantics) analytics.

Usage, from ``backend/``:

    uv run python scripts/sample_day.py                  # writes to <repo>/data
    uv run python scripts/sample_day.py --data-root /tmp/d

Exit code 0 means every output table holds rows for the trade date.
"""

from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_SRC_ROOT = _BACKEND_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

_CONFIG_PATH = _BACKEND_ROOT.parent / "configs" / "default.toml"
_DEFAULT_DATA_ROOT = _BACKEND_ROOT.parent / "data"

from config import config_hash, load_config  # noqa: E402
from connectivity import ManualClock  # noqa: E402
from contracts import InstrumentKey, InstrumentMaster, Position, RawMarketEvent  # noqa: E402
from fixtures.events import quote_events  # noqa: E402
from fixtures.library import ChainFixture, get_fixture  # noqa: E402
from orchestration import run_incremental_analytics  # noqa: E402
from storage import ParquetStore  # noqa: E402

# The offline fixture the SAMPLE provider replays — same one as the e2e/golden tests.
_FIXTURE_NAME = "synthetic_known_answer"
# The paper book priced against the chain: long 10 ATM calls, short 5 ATM puts,
# long 3 first-OTM calls — mirrors the pf-risk shape the risk tests use, expressed
# over the chain's real contract keys so the valuation join resolves.
_PORTFOLIO_ID = "pf-sample"
_BOOK = (("C", 0, 10.0), ("P", 0, -5.0), ("C", 1, 3.0))  # (right, strike rank from ATM, qty)


def _chain_inputs(
    chain: ChainFixture,
) -> tuple[list[RawMarketEvent], list[InstrumentKey], list[InstrumentMaster]]:
    """Fixture chain → the (events, instruments, masters) the analytics job needs.

    Mirrors the orchestration test scaffolding (tests cannot be imported from a
    script): one bid/ask/last triplet per instrument at the chain's as-of.
    """
    spot = chain.underlying_spot
    events = list(
        quote_events(
            chain.underlying,
            bid=spot - 0.05,
            ask=spot + 0.05,
            last=spot,
            ts=chain.as_of,
            session_id=chain.underlying.canonical(),
        )
    )
    instruments = [chain.underlying]
    masters = [_master(chain, chain.underlying)]
    for quote in chain.quotes:
        events += list(
            quote_events(
                quote.instrument,
                bid=quote.bid,
                ask=quote.ask,
                last=quote.last,
                ts=chain.as_of,
                session_id=quote.instrument.canonical(),
            )
        )
        instruments.append(quote.instrument)
        masters.append(_master(chain, quote.instrument))
    return events, instruments, masters


def _master(chain: ChainFixture, instrument: InstrumentKey) -> InstrumentMaster:
    return InstrumentMaster(
        instrument_key=instrument.canonical(),
        as_of_date=chain.as_of.date(),
        instrument=instrument,
        raw_broker_payload="{}",
    )


def _book_positions(chain: ChainFixture) -> list[Position]:
    """Build the paper book over the chain's real contract keys."""
    spot = chain.underlying_spot
    by_right: dict[str, list[InstrumentKey]] = {"C": [], "P": []}
    for quote in chain.quotes:
        right = quote.instrument.option_right
        if right in by_right:
            by_right[right].append(quote.instrument)
    positions: list[Position] = []
    for right, rank, quantity in _BOOK:
        contracts = sorted(by_right[right], key=lambda i: (abs(i.strike - spot), i.strike))
        if rank >= len(contracts):
            raise SystemExit(f"chain has no rank-{rank} {right} contract for the book")
        positions.append(
            Position(
                valuation_ts=chain.as_of,
                portfolio_id=_PORTFOLIO_ID,
                contract_key=contracts[rank].canonical(),
                quantity=quantity,
                source="record",
            )
        )
    return positions


def _seed_raw_layer(
    store: ParquetStore,
    chain: ChainFixture,
    events: list[RawMarketEvent],
    masters: list[InstrumentMaster],
) -> bool:
    """Write the raw layer once; skip when the trade date is already on disk."""
    trade_date = chain.as_of.date()
    existing = set(store.list_partitions("raw_market_events"))
    if (trade_date, chain.underlying.underlying_symbol) in existing:
        return False
    store.write("raw_market_events", events)
    store.write("instrument_master", masters)
    return True


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-root",
        type=Path,
        default=_DEFAULT_DATA_ROOT,
        help=f"ParquetStore root to populate (default: {_DEFAULT_DATA_ROOT})",
    )
    args = parser.parse_args(argv)

    chain = get_fixture(_FIXTURE_NAME)
    trade_date = chain.as_of.date()
    events, instruments, masters = _chain_inputs(chain)
    positions = _book_positions(chain)

    store = ParquetStore(args.data_root)
    seeded = _seed_raw_layer(store, chain, events, masters)
    # The book is an input, not a derived value: run_incremental_analytics does not
    # persist it, but the frontend needs it to reprice scenarios on demand.
    # Replace-semantics table, so re-running is safe.
    store.write("positions", positions)
    config = load_config(_CONFIG_PATH)

    result = run_incremental_analytics(
        store=store,
        config=config,
        config_hash=config_hash(config),
        positions=positions,
        instruments=instruments,
        masters=masters,
        trade_date=trade_date,
        as_of=chain.as_of,
        calc_ts=chain.as_of + timedelta(minutes=30),
        clock=ManualClock(start=chain.as_of + timedelta(minutes=30)),
        correlation_id="sample-day",
    )

    outputs = result.outputs
    counts = {
        "raw_events_seeded": len(events) if seeded else 0,
        "snapshots": len(outputs.snapshots),
        "forwards": len(outputs.forwards),
        "iv_points": len(outputs.iv_points),
        "surface_parameters": len(outputs.surface_parameters),
        "surface_grid": len(outputs.surface_grid),
        "pricing_results": len(outputs.pricings),
        "risk_aggregates": len(outputs.risk_aggregates),
        "scenario_results": len(outputs.scenarios),
    }
    print(f"trade_date={trade_date} underlying={chain.underlying.underlying_symbol} "
          f"portfolio={_PORTFOLIO_ID} data_root={args.data_root}")
    for name, count in counts.items():
        print(f"  {name}: {count}")

    derived = {name: count for name, count in counts.items() if name != "raw_events_seeded"}
    return 0 if all(count > 0 for count in derived.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
