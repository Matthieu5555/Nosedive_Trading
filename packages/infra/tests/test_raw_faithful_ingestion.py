"""Raw is faithful; the two-sided gate is a DERIVED concern (blueprint 01-architecture §13/§39).

The blueprint splits the stack into layers and forbids a downstream layer from silently
overwriting an upstream observation: raw capture records *every* tick, and reproducibility is
defined from those raw observations. So a closed-market / one-sided option quote (the 2026-06-15
canary, ``bid==ask<=0`` with only ``last`` real) must:

* still be PERSISTED as a flagged :class:`MarketStateSnapshot` (faithful — the observation is not
  erased), and
* be EXCLUDED from the IV solver by the derived-layer two-sided gate
  (``actor.driver._has_two_sided_option_quote``) — never priced as if it were a real quote.

This end-to-end test drives ``run_analytics`` over a known-answer chain with exactly one poisoned
option and asserts both halves. The oracle is independent of the analytics code: the poisoned
contract's own instrument key (from the fixture) — its snapshot must be present, its IV point must
be absent, and the healthy contracts must still price.
"""

from __future__ import annotations

from datetime import UTC, datetime

from algotrading.core.config import (
    PlatformConfig,
    QcThresholdConfig,
    ScenarioConfig,
    SolverConfig,
    UniverseConfig,
)
from algotrading.infra.actor import run_analytics
from algotrading.infra.contracts import InstrumentMaster
from algotrading.infra.contracts.instrument_key import InstrumentKey
from fixtures.events import quote_events
from fixtures.library import FORWARD_CONFIG, SURFACE_CONFIG, get_fixture

AS_OF = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)
CALC_TS = datetime(2026, 5, 29, 16, 0, tzinfo=UTC)
CONFIG_HASH = {"cfg": "cfg-hash-raw-faithful"}


def _config() -> PlatformConfig:
    return PlatformConfig(
        universe=UniverseConfig(version="u-1", exchange="SMART"),
        qc_threshold=QcThresholdConfig(
            version="qc-1", max_spread_pct=0.5, max_quote_age_seconds=30.0, min_chain_count=1
        ),
        solver=SolverConfig(version="iv-1", iv_tolerance=1e-12, max_iterations=200),
        surface=SURFACE_CONFIG,
        forward=FORWARD_CONFIG,
        scenario=ScenarioConfig(
            version="scn-1", spot_shocks=(-0.05, 0.05), vol_shocks=(0.05, -0.05)
        ),
    )


def _master(instrument: InstrumentKey) -> InstrumentMaster:
    return InstrumentMaster(
        instrument_key=instrument.canonical(),
        as_of_date=AS_OF.date(),
        instrument=instrument,
        raw_broker_payload="{}",
    )


def test_canary_option_lands_in_raw_but_is_excluded_from_iv() -> None:
    chain = get_fixture("synthetic_known_answer")
    spot = chain.underlying_spot

    # Poison exactly one option: a closed-market quote (bid==ask==0) with only `last` real — the
    # 2026-06-15 canary shape. Every other contract keeps its healthy two-sided quote.
    poisoned = chain.quotes[0].instrument
    poisoned_key = poisoned.canonical()

    events = list(
        quote_events(
            chain.underlying, bid=spot - 0.05, ask=spot + 0.05, last=spot, ts=AS_OF,
            session_id=chain.underlying.canonical(),
        )
    )
    instruments = [chain.underlying]
    masters = [_master(chain.underlying)]
    for quote in chain.quotes:
        is_poisoned = quote.instrument.canonical() == poisoned_key
        events += list(
            quote_events(
                quote.instrument,
                bid=0.0 if is_poisoned else quote.bid,
                ask=0.0 if is_poisoned else quote.ask,
                last=quote.last,  # last stays real, so a (fallback) snapshot is still built
                ts=AS_OF,
                session_id=quote.instrument.canonical(),
            )
        )
        instruments.append(quote.instrument)
        masters.append(_master(quote.instrument))

    outputs = run_analytics(
        events, [], instruments=instruments, masters=masters,
        config=_config(), config_hashes=CONFIG_HASH, as_of=AS_OF, calc_ts=CALC_TS,
    )

    snapshot_keys = {s.instrument_key for s in outputs.snapshots}
    iv_keys = {p.contract_key for p in outputs.iv_points}

    # FAITHFUL: the poisoned observation is recorded as a snapshot, not erased at capture/normalize.
    assert poisoned_key in snapshot_keys, "the canary quote must still be persisted (faithful raw)"
    # DERIVED GATE: it is never priced — no IV point comes from a one-sided / zero quote.
    assert poisoned_key not in iv_keys, "the canary quote must not produce an IV point"
    # SANITY: the analytics still ran — healthy contracts priced, so the absence above is the gate,
    # not an empty run. Every IV point traces to a (non-poisoned) persisted snapshot.
    assert iv_keys, "the healthy contracts must still produce IV points"
    assert iv_keys <= snapshot_keys
