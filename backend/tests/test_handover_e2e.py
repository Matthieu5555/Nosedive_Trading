"""The scripted "new engineer" end-to-end handover test.

This is the executable form of Workstream E's "new engineer" acceptance criterion
(`tasks/05-integration-operations.md`): on a fresh checkout, a person who has never
seen this code must be able to set up the environment, run a connectivity smoke test,
trigger a replay, and read a QC report — driving only the documented entrypoints. This
test walks that exact path and asserts a real artifact at every stage, so the runbooks
in ``documentation/`` cannot silently drift from the code: if a documented command stops
working, this test goes red.

Each stage drives the same public APIs the runbooks tell an operator to call:

* (a) bootstrap — ``config.load_config`` over ``configs/default.toml`` yields a valid,
  hashable ``PlatformConfig`` and a ``ParquetStore`` opens on a data root;
* (b) connectivity smoke — the ``connectivity`` + ``universe`` + ``collectors`` path
  from ``tests/test_smoke_bootstrap.py``: resolve one contract, capture one quote, and
  place no orders;
* (c) triggered replay — ``reconstruction.reconstruct_day`` over a seeded raw day runs
  the *identical* actor compute as live and persists derived outputs (snapshots,
  forwards, IV, surface, pricing, risk, scenarios);
* (d) QC report — ``orchestration.run_qc`` rolls a readable report, writes the
  ``QcResult`` rows, and reports an escalation level.

The named fixture library (``fixtures.library.get_fixture("synthetic_known_answer")``)
and the chain-driving pattern (events/instruments/masters → seed the raw layer → run)
are reused verbatim from ``tests/test_replay_byte_identical.py`` so this test exercises
the same rich path the headline replay test does, not a hollow smoke.

Scope note: the replay stage drives a single seeded day, not a historical month. The
month-scale reconstruction is already pinned by
``tests/test_replay_reconstruction.py``; this test's job is the new-engineer *path*
end to end, so one day is the deliberate, sufficient scope here — stated out loud
rather than left implicit.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from collectors import (
    MarketDataCollector,
    summarize_session,
)
from config import PlatformConfig, config_hash, load_config
from connectivity import (
    BrokerTick,
    FakeBrokerSession,
    ManualClock,
    SessionSupervisor,
    client_id_for,
)
from contracts import InstrumentMaster, Position
from contracts.instrument_key import InstrumentKey
from fixtures.events import quote_events
from fixtures.library import ChainFixture, get_fixture
from orchestration import run_qc
from orchestration.reconstruction import RECONSTRUCTED, reconstruct_day
from qc import STATUS_PASS, thresholds_from_config
from storage import ParquetStore

# The economics config the runbooks load — the real checked-in file, not a stub. Using
# the shipped config is the point: the new engineer runs against what production runs.
_CONFIG_PATH = Path(__file__).resolve().parents[2] / "configs" / "default.toml"

# Injected times (nothing reads a clock), matching the replay fixtures' AS_OF so the
# synthetic-known-answer chain inverts cleanly.
_AS_OF = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)
_CALC_TS = datetime(2026, 5, 29, 16, 0, tzinfo=UTC)

# The connectivity smoke uses its own trade date and broker rows, mirroring
# tests/test_smoke_bootstrap.py — one resolvable contract, one quote, no orders.
_SMOKE_TRADE_DATE = date(2026, 6, 1)
_SMOKE_T0 = datetime(2026, 6, 1, 13, 30, tzinfo=UTC)
_SMOKE_UNDERLYING_ROW: dict[str, object] = {
    "conId": "u-AAPL", "symbol": "AAPL", "secType": "STK", "exchange": "SMART",
    "currency": "USD", "multiplier": 1,
}
_SMOKE_OPTION_ROW: dict[str, object] = {
    "conId": "o-AAPL-C-100", "symbol": "AAPL", "secType": "OPT", "exchange": "SMART",
    "currency": "USD", "multiplier": 100, "expiry": "20260619", "strike": 100, "right": "C",
}


def _master(instrument: InstrumentKey) -> InstrumentMaster:
    return InstrumentMaster(
        instrument_key=instrument.canonical(),
        as_of_date=_AS_OF.date(),
        instrument=instrument,
        raw_broker_payload="{}",
    )


def _chain_inputs(
    chain: ChainFixture,
) -> tuple[list, list[InstrumentKey], list[InstrumentMaster]]:
    """A named chain fixture as the (events, instruments, masters) the actor consumes.

    Per-instrument session ids keep ``(session_id, event_id)`` unique, so the events are
    a valid append-only raw batch the replay path can seed and read back. This is the
    same shaping ``tests/test_replay_byte_identical.py`` uses.
    """
    spot = chain.underlying_spot
    events = list(
        quote_events(
            chain.underlying, bid=spot - 0.05, ask=spot + 0.05, last=spot, ts=_AS_OF,
            session_id=chain.underlying.canonical(),
        )
    )
    instruments = [chain.underlying]
    masters = [_master(chain.underlying)]
    for quote in chain.quotes:
        events += list(
            quote_events(
                quote.instrument, bid=quote.bid, ask=quote.ask, last=quote.last, ts=_AS_OF,
                session_id=quote.instrument.canonical(),
            )
        )
        instruments.append(quote.instrument)
        masters.append(_master(quote.instrument))
    return events, instruments, masters


def _positions(chain: ChainFixture) -> list[Position]:
    """A small long/short book over the chain's calls, so risk and scenarios are exercised."""
    calls = [q.instrument for q in chain.quotes if q.instrument.option_right == "C"]
    return [
        Position(
            valuation_ts=_AS_OF, portfolio_id="pf-handover", contract_key=contract.canonical(),
            quantity=quantity, source="record",
        )
        for contract, quantity in zip(calls[:3], [10.0, -5.0, 3.0], strict=False)
    ]


def test_handover_new_engineer_path_end_to_end(tmp_path: Path) -> None:
    """Drive the documented new-engineer path and assert an artifact at each stage."""

    # -- stage (a): bootstrap is usable -------------------------------------
    # The runbooks open with `config.load_config(configs/default.toml)` and a
    # ParquetStore on a data root. Both must produce a real, hashable object.
    config: PlatformConfig = load_config(_CONFIG_PATH)
    assert config.universe.underlyings, "the shipped config must name underlyings to trade"
    cfg_hash = config_hash(config)
    assert isinstance(cfg_hash, str) and cfg_hash, "config_hash must produce a stable string"

    store = ParquetStore(tmp_path / "data")
    assert store.root == tmp_path / "data", "the store opens on the requested data root"
    thresholds = thresholds_from_config(config.qc_threshold)
    assert thresholds.threshold_version == config.qc_threshold.version

    # -- stage (b): a connectivity smoke test passes ------------------------
    # The start-of-day runbook's smoke: resolve one contract off a (fake) broker
    # session, capture exactly one quote, and place no orders. Mirrors
    # tests/test_smoke_bootstrap.py against the documented APIs.
    from universe import UniverseService, materialize_universe

    smoke_store = ParquetStore(tmp_path / "smoke")
    clock = ManualClock(start=_SMOKE_T0)
    one_quote = [
        BrokerTick(
            "o-AAPL-C-100", "bid", 5.25, sequence=1,
            exchange_ts=_SMOKE_T0 + timedelta(seconds=1),
        )
    ]
    session = FakeBrokerSession(
        chains={"AAPL": (_SMOKE_UNDERLYING_ROW, _SMOKE_OPTION_ROW)}, script=one_quote
    )
    supervisor = SessionSupervisor(session, client_id=client_id_for("smoke"), clock=clock)
    supervisor.connect()
    rows = supervisor.request_option_chain("AAPL")
    materialize_universe(smoke_store, rows, _SMOKE_TRADE_DATE)
    universe = UniverseService.load_active_universe(smoke_store, _SMOKE_TRADE_DATE)
    smoke_option = universe.get_option_chain("AAPL", _SMOKE_TRADE_DATE)[0]
    collector = MarketDataCollector(
        store=smoke_store, universe=universe,
        session_id="handover-smoke", trade_date=_SMOKE_TRADE_DATE, clock=clock,
    )
    smoke_summary = collector.collect(supervisor, subscribe=[smoke_option.broker_contract_id])

    smoke_events = smoke_store.read("raw_market_events")
    assert len(smoke_events) == 1, "the smoke must capture exactly one quote"
    assert smoke_events[0].value == 5.25
    assert smoke_summary.event_count == 1
    # No order placement anywhere in the system: the positions layer stays empty.
    assert smoke_store.list_partitions("positions") == []

    # -- stage (c): a triggered replay produces derived outputs -------------
    # Seed a known-answer raw day, then trigger reconstruction.reconstruct_day exactly
    # as the replay/backfill runbook documents. The actor's identical-to-live compute
    # must land every derived table.
    chain = get_fixture("synthetic_known_answer")
    events, instruments, masters = _chain_inputs(chain)
    positions = _positions(chain)

    store.write("raw_market_events", events)
    day = reconstruct_day(
        store, _AS_OF.date(), positions,
        instruments=instruments, masters=masters,
        config=config, config_hash=cfg_hash,
        as_of=_AS_OF, calc_ts=_CALC_TS,
        correlation_id="handover-replay", persist=True,
    )

    assert day.status == RECONSTRUCTED, "a seeded raw day must reconstruct, not be MISSING/EMPTY"
    assert day.outputs is not None and not day.outputs.is_empty()
    assert day.record_count > 0
    # Every derived layer the runbook promises actually landed on disk.
    assert len(store.read("market_state_snapshots")) > 0, "no snapshots persisted"
    assert len(store.read("forward_curve")) > 0, "no forward curve persisted"
    assert len(store.read("iv_points")) > 0, "no IV points persisted"
    assert len(store.read("surface_parameters")) > 0, "no surface parameters persisted"
    assert len(store.read("pricing_results")) == len(positions), "one pricing per held contract"
    assert len(store.read("risk_aggregates")) > 0, "no portfolio risk aggregate persisted"
    assert len(store.read("scenario_results")) > 0, "no scenario grid persisted"

    # -- stage (d): a QC report is generated and is readable ----------------
    # Summarize the day's capture, then run the QC job exactly as the EOD runbook
    # documents. It must roll a report, write QcResult rows, and report escalation.
    subscribed = [instrument.canonical() for instrument in instruments]
    summary = summarize_session(
        events, session_id="handover", trade_date=_AS_OF.date(),
        subscribed_keys=subscribed, reconnect_count=0,
    )
    qc = run_qc(
        store=store, thresholds=thresholds, collector_summary=summary,
        trade_date=_AS_OF.date(), run_id="handover-run", run_ts=_CALC_TS,
        correlation_id="handover", persist=True,
    )

    # The report is real: a result row was produced, persisted, and is readable back.
    assert len(qc.results) >= 1, "the QC job must produce at least the continuity result"
    assert qc.overall_status in {"pass", "warn", "fail"}
    assert qc.escalation in {"none", "notice", "page"}
    # A clean synthetic day with full coverage and no gaps passes its continuity check.
    assert qc.overall_status == STATUS_PASS, "a clean synthetic day should pass QC"
    assert qc.escalation == "none"
    persisted_qc = store.read("qc_results")
    assert len(persisted_qc) == len(qc.results), "every QcResult row must persist"
    assert persisted_qc[0].run_id == "handover-run", "the persisted row carries the injected run id"
