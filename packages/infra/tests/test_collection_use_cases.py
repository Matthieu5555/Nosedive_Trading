from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from algotrading.core.config import (
    PlatformConfig,
    QcThresholdConfig,
    ScenarioConfig,
    SolverConfig,
    UniverseConfig,
)
from algotrading.infra.actor import run_analytics
from algotrading.infra.collectors import (
    BrokerTick,
    RawCollector,
    ReplaySource,
    next_sequence,
    replay_day,
)
from algotrading.infra.connectivity import ManualClock
from algotrading.infra.contracts import InstrumentKey, InstrumentMaster
from algotrading.infra.orchestration import (
    ProviderCapture,
    SurfaceJobRequest,
    build_surface,
    run_provider_flow,
)
from algotrading.infra.storage import ParquetStore
from algotrading.infra.storage.partitioning import table_dir
from fixtures.library import FORWARD_CONFIG, SURFACE_CONFIG, ChainFixture, get_fixture

_AS_OF = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)
_CALC_TS = datetime(2026, 5, 29, 16, 0, tzinfo=UTC)
_TRADE_DATE = _AS_OF.date()
_CONFIG_HASH = {"cfg": "cfg-hash-usecases"}
_MARKET_DATA_TYPE = 3


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
        as_of_date=_AS_OF.date(),
        instrument=instrument,
        raw_broker_payload="{}",
    )


def _capture_ticks(chain: ChainFixture) -> tuple[list[BrokerTick], list[InstrumentMaster]]:
    spot = chain.underlying_spot
    counters: dict[tuple[str, str], int] = {}
    ticks: list[BrokerTick] = []
    masters: list[InstrumentMaster] = [_master(chain.underlying)]

    def _add(instrument: InstrumentKey, bid: float, ask: float, last: float) -> None:
        key = instrument.canonical()
        for field, value in (("bid", bid), ("ask", ask), ("last", last)):
            ticks.append(
                BrokerTick(
                    instrument_key=key, field_name=field, value=value,
                    underlying=instrument.underlying_symbol,
                    sequence=next_sequence(counters, key, field), exchange_ts=_AS_OF,
                )
            )

    _add(chain.underlying, spot - 0.05, spot + 0.05, spot)
    for quote in chain.quotes:
        _add(quote.instrument, quote.bid, quote.ask, quote.last)
        masters.append(_master(quote.instrument))
    return ticks, masters


class _FakePushAdapter:

    def __init__(self, ticks: Sequence[BrokerTick]) -> None:
        self._ticks = list(ticks)
        self._tick_cb = None

    def subscribe(self, instrument_keys: object) -> None: ...
    def set_tick_callback(self, callback) -> None:  # type: ignore[no-untyped-def]
        self._tick_cb = callback
    def set_fault_callback(self, callback) -> None:  # type: ignore[no-untyped-def]
        ...
    def unsubscribe_all(self) -> None: ...

    def pump(self, _collector: RawCollector) -> None:
        for tick in self._ticks:
            self._tick_cb(tick)  # type: ignore[misc]


def test_build_surface_captures_quotes_and_fits_a_surface(tmp_path: Path) -> None:
    chain = get_fixture("synthetic_known_answer")
    ticks, masters = _capture_ticks(chain)
    store = ParquetStore(tmp_path)
    adapter = _FakePushAdapter(ticks)

    result = build_surface(
        request=SurfaceJobRequest(
            symbol="AAPL", trade_date=_TRADE_DATE, market_data_type=_MARKET_DATA_TYPE,
            as_of=_AS_OF, calc_ts=_CALC_TS,
        ),
        store=store, config=_config(), config_hashes=_CONFIG_HASH,
        adapter=adapter, masters=masters, drive=adapter.pump,
        clock=ManualClock(start=_AS_OF), correlation_id="surface-corr",
    )

    assert result.collection.event_count == len(ticks)
    assert not result.outputs.is_empty()
    assert result.fitted_maturities >= 1
    assert result.market_data_status.subscribed > 0
    assert len(store.read("surface_parameters")) > 0


def test_build_surface_status_reflects_no_diagnostics(tmp_path: Path) -> None:
    chain = get_fixture("synthetic_known_answer")
    ticks, masters = _capture_ticks(chain)
    store = ParquetStore(tmp_path)
    adapter = _FakePushAdapter(ticks)
    result = build_surface(
        request=SurfaceJobRequest(
            symbol="AAPL", trade_date=_TRADE_DATE, market_data_type=_MARKET_DATA_TYPE,
            as_of=_AS_OF, calc_ts=_CALC_TS, persist=False,
        ),
        store=store, config=_config(), config_hashes=_CONFIG_HASH,
        adapter=adapter, masters=masters, drive=adapter.pump,
        clock=ManualClock(start=_AS_OF), correlation_id="surface-corr",
        diagnostics=None,
    )
    assert result.market_data_status.requested_type == _MARKET_DATA_TYPE


def test_provider_flow_captures_two_providers_into_one_raw_layer(tmp_path: Path) -> None:
    chain = get_fixture("synthetic_known_answer")
    ticks, _ = _capture_ticks(chain)
    keys = sorted({t.instrument_key for t in ticks})
    half = len(ticks) // 2
    store = ParquetStore(tmp_path)

    adapter_a = _FakePushAdapter(ticks[:half])
    adapter_b = _FakePushAdapter(ticks[half:])
    result = run_provider_flow(
        store=store,
        providers=[
            ProviderCapture(provider="DERIBIT", adapter=adapter_a,
                            subscribe=keys, drive=adapter_a.pump),
            ProviderCapture(provider="SAXO", adapter=adapter_b,
                            subscribe=keys, drive=adapter_b.pump),
        ],
        trade_date=_TRADE_DATE, clock=ManualClock(start=_AS_OF), correlation_id="pf-corr",
    )

    assert len(result.captures) == 2
    all_events = store.read("raw_market_events")
    assert len(all_events) == len(ticks)
    assert result.total_events == len(ticks)
    sessions = {e.session_id for e in all_events}
    assert sessions == {f"deribit-{_TRADE_DATE.isoformat()}", f"saxo-{_TRADE_DATE.isoformat()}"}


def _partition_bytes(store: ParquetStore, table: str) -> dict[str, bytes]:
    base = table_dir(store.root, table)
    if not base.exists():
        return {}
    return {
        str(path.relative_to(base)): path.read_bytes()
        for path in sorted(base.glob("**/*.parquet"))
    }


def _capture_live(store: ParquetStore, ticks: list[BrokerTick], keys: list[str], clock_start):  # type: ignore[no-untyped-def]
    adapter = _FakePushAdapter(ticks)
    collector = RawCollector(
        store=store, adapter=adapter, session_id="live-day",
        trade_date=_TRADE_DATE, clock=ManualClock(start=clock_start), subscribed_keys=keys,
    )
    collector.start(keys)
    adapter.pump(collector)
    collector.close()
    return replay_day(store, _TRADE_DATE)


def test_replaying_a_captured_day_into_the_same_store_is_a_byte_identical_no_op(
    tmp_path: Path,
) -> None:
    chain = get_fixture("synthetic_known_answer")
    ticks, masters = _capture_ticks(chain)
    keys = [m.instrument.canonical() for m in masters]
    store = ParquetStore(tmp_path)

    captured = _capture_live(store, ticks, keys, _AS_OF)
    assert captured, "the live capture produced events"
    before = _partition_bytes(store, "raw_market_events")

    replay_source = ReplaySource(captured)
    replay = RawCollector(
        store=store, adapter=replay_source, session_id="live-day",
        trade_date=_TRADE_DATE, clock=ManualClock(start=_CALC_TS), subscribed_keys=keys,
    )
    replay_source.pump()
    replay.close()

    after = _partition_bytes(store, "raw_market_events")
    assert after == before, "re-capturing the day must leave the raw partition byte-identical"


def test_live_and_replay_capture_yield_identical_content_and_derived_outputs(
    tmp_path: Path,
) -> None:
    chain = get_fixture("synthetic_known_answer")
    ticks, masters = _capture_ticks(chain)
    instruments = [m.instrument for m in masters]
    keys = [m.instrument.canonical() for m in masters]

    live_store = ParquetStore(tmp_path / "live")
    captured = _capture_live(live_store, ticks, keys, _AS_OF)

    replay_store = ParquetStore(tmp_path / "replay")
    replay_source = ReplaySource(captured)
    replay = RawCollector(
        store=replay_store, adapter=replay_source, session_id="live-day",
        trade_date=_TRADE_DATE, clock=ManualClock(start=_CALC_TS), subscribed_keys=keys,
    )
    replay_source.pump()
    replay.close()

    live_events = replay_day(live_store, _TRADE_DATE)
    replay_events = replay_day(replay_store, _TRADE_DATE)
    assert [(e.event_id, e.value) for e in live_events] == [
        (e.event_id, e.value) for e in replay_events
    ]

    live_out = run_analytics(
        live_events, [], instruments=instruments, masters=masters,
        config=_config(), config_hashes=_CONFIG_HASH, as_of=_AS_OF, calc_ts=_CALC_TS,
    )
    replay_out = run_analytics(
        replay_events, [], instruments=instruments, masters=masters,
        config=_config(), config_hashes=_CONFIG_HASH, as_of=_AS_OF, calc_ts=_CALC_TS,
    )
    assert live_out == replay_out
    assert not live_out.is_empty()
