"""The scripted "new engineer" end-to-end handover test (engine path).

This is the executable form of Workstream E's "new engineer" acceptance criterion: on
a fresh checkout, a person who has never seen this code must be able to set up the
environment, trigger a replay, and read a QC report — driving only the documented
entrypoints. This test walks that path and asserts a real artifact at every stage, so
the runbooks in ``documentation/`` cannot silently drift from the code: if a documented
command stops working, this test goes red.

The stages drive the same public APIs the runbooks tell an operator to call:

* (a) bootstrap — ``load_platform_config(configs/)`` yields a valid,
  hashable ``PlatformConfig`` and a ``ParquetStore`` opens on a data root;
* (c) triggered replay — ``orchestration.reconstruction.reconstruct_day`` over a seeded
  raw day runs the *identical* actor compute as live and persists derived outputs
  (snapshots, forwards, IV, surface, pricing, risk, scenarios);
* (d) QC report — ``orchestration.run_qc`` rolls a readable report, writes the
  ``QcResult`` rows, and reports an escalation level.

Relocated onto the ``packages/`` stack (C3) and re-pointed to ``algotrading.infra.*``,
driving the ported actor. Stage **(b) connectivity smoke** — resolve a contract, capture
exactly one quote through the one unified collector, place no orders — now runs on the
unified collection seam (C6 / ADR 0027): a fake push adapter feeds the single
``RawCollector``, which writes the one canonical ``RawMarketEvent`` shape.

The named fixture library (``fixtures.library.get_fixture("synthetic_known_answer")``)
and the chain-driving pattern (events/instruments/masters → seed the raw layer → run)
are reused verbatim from ``test_replay_byte_identical.py`` so this exercises the same
rich path the headline replay test does, not a hollow smoke.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from algotrading.core.config import PlatformConfig, config_hashes, load_platform_config
from algotrading.infra.collectors import BrokerTick, RawCollector, summarize_session
from algotrading.infra.connectivity import ManualClock
from algotrading.infra.contracts import InstrumentMaster, Position
from algotrading.infra.contracts.instrument_key import InstrumentKey
from algotrading.infra.orchestration import collect_live, run_qc
from algotrading.infra.orchestration.reconstruction import RECONSTRUCTED, reconstruct_day
from algotrading.infra.qc import STATUS_PASS, thresholds_from_config
from algotrading.infra.storage import ParquetStore
from algotrading.infra.universe import UniverseService, materialize_universe
from fixtures.events import quote_events
from fixtures.library import ChainFixture, get_fixture

# The economics config the runbooks load — the real checked-in bundles, not a stub. Using
# the shipped config is the point: the new engineer runs against what production runs.
_CONFIGS_DIR = Path(__file__).resolve().parents[3] / "configs"

# Injected times (nothing reads a clock), matching the replay fixtures' AS_OF so the
# synthetic-known-answer chain inverts cleanly.
_AS_OF = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)
_CALC_TS = datetime(2026, 5, 29, 16, 0, tzinfo=UTC)


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
    same shaping ``test_replay_byte_identical.py`` uses.
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
    """Drive the documented new-engineer engine path and assert an artifact at each stage."""

    # -- stage (a): bootstrap is usable -------------------------------------
    # The runbooks open with `load_platform_config(configs/)` (the six Part VII bundles)
    # and a ParquetStore on a data root. Both must produce a real, hashable object.
    config: PlatformConfig = load_platform_config(_CONFIGS_DIR)
    assert config.universe.indices, "the shipped config must name the indices to trade"
    cfg_hashes = config_hashes(config)
    assert cfg_hashes and all(cfg_hashes.values()), "config_hashes must name every bundle"

    store = ParquetStore(tmp_path / "data")
    assert store.root == tmp_path / "data", "the store opens on the requested data root"
    thresholds = thresholds_from_config(config.qc_threshold)
    assert thresholds.version == config.qc_threshold.version

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
        config=config, config_hashes=cfg_hashes,
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


# The connectivity smoke uses its own small trade date and one resolvable contract — one
# quote, no orders — mirroring the start-of-day runbook's smoke step.
_SMOKE_TRADE_DATE = _AS_OF.date()
_SMOKE_ROWS: list[dict[str, object]] = [
    {"conId": "u", "symbol": "AAPL", "secType": "STK", "exchange": "SMART",
     "currency": "USD", "multiplier": 1},
    {"conId": "c1", "symbol": "AAPL", "secType": "OPT", "exchange": "SMART",
     "currency": "USD", "multiplier": 100, "expiry": "20260619", "strike": 100, "right": "C"},
]


class _OneQuoteAdapter:
    """A push MarketDataAdapter that emits exactly one quote for one instrument when driven."""

    def __init__(self, instrument_key: str, *, value: float) -> None:
        self._key = instrument_key
        self._value = value
        self._tick_cb = None

    def subscribe(self, instrument_keys: object) -> None: ...
    def set_tick_callback(self, callback) -> None:  # type: ignore[no-untyped-def]
        self._tick_cb = callback
    def set_fault_callback(self, callback) -> None:  # type: ignore[no-untyped-def]
        ...
    def unsubscribe_all(self) -> None: ...

    def pump(self, _collector: RawCollector) -> None:
        self._tick_cb(  # type: ignore[misc]
            BrokerTick(
                instrument_key=self._key, field_name="bid", value=self._value,
                underlying="AAPL", exchange_ts=_AS_OF,
            )
        )


def test_handover_connectivity_smoke(tmp_path: Path) -> None:
    """Stage (b): resolve a contract, capture exactly one quote, place no orders.

    Drives the documented smoke path on the one unified collector: materialize a tiny universe
    off (fake) broker rows, pick one option, and capture a single quote through ``collect_live``
    — the same collector the live and replay paths use. Asserts exactly one ``RawMarketEvent``
    landed and that nothing was written to the positions/orders layer.
    """
    smoke_store = ParquetStore(tmp_path / "smoke")
    materialize_universe(smoke_store, _SMOKE_ROWS, _SMOKE_TRADE_DATE)
    universe = UniverseService.load_active_universe(smoke_store, _SMOKE_TRADE_DATE)
    option = universe.get_option_chain("AAPL", _SMOKE_TRADE_DATE)[0]
    option_key = option.canonical()

    adapter = _OneQuoteAdapter(option_key, value=5.25)
    result = collect_live(
        store=smoke_store, adapter=adapter, subscribe=[option_key],
        session_id="handover-smoke", trade_date=_SMOKE_TRADE_DATE,
        clock=ManualClock(start=_AS_OF), drive=adapter.pump, correlation_id="handover-smoke",
    )

    smoke_events = smoke_store.read("raw_market_events")
    assert len(smoke_events) == 1, "the smoke must capture exactly one quote"
    assert smoke_events[0].value == 5.25
    assert smoke_events[0].instrument_key == option_key
    assert result.summary.event_count == 1
    # No orders/positions are placed by a connectivity smoke.
    assert smoke_store.read("positions") == []
