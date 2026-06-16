from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from algotrading.core.config import (
    PlatformConfig,
    QcThresholdConfig,
    ScenarioConfig,
    SolverConfig,
    UniverseConfig,
)
from algotrading.infra.contracts import InstrumentMaster, Position, RawMarketEvent
from algotrading.infra.contracts.instrument_key import InstrumentKey
from algotrading.infra.orchestration.reconstruction import (
    EMPTY,
    MISSING,
    RECONSTRUCTED,
    compare_replay_to_live,
    reconstruct_day,
    reconstruct_range,
    stored_trade_dates,
)
from algotrading.infra.storage import ParquetStore
from fixtures.events import quote_events
from fixtures.library import FORWARD_CONFIG, SURFACE_CONFIG, ChainFixture, get_fixture


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


def _master(instrument: InstrumentKey, as_of_date: date) -> InstrumentMaster:
    return InstrumentMaster(
        instrument_key=instrument.canonical(),
        as_of_date=as_of_date,
        instrument=instrument,
        raw_broker_payload="{}",
    )


def _as_of(trade_date: date) -> datetime:
    return datetime(trade_date.year, trade_date.month, trade_date.day, 15, 30, tzinfo=UTC)


def _calc_ts(trade_date: date) -> datetime:
    return datetime(trade_date.year, trade_date.month, trade_date.day, 16, 0, tzinfo=UTC)


def _day_events(chain: ChainFixture, trade_date: date) -> list[RawMarketEvent]:
    as_of = _as_of(trade_date)
    spot = chain.underlying_spot
    events = list(
        quote_events(
            chain.underlying,
            bid=spot - 0.05,
            ask=spot + 0.05,
            last=spot,
            ts=as_of,
            session_id=chain.underlying.canonical(),
        )
    )
    for quote in chain.quotes:
        events += list(
            quote_events(
                quote.instrument,
                bid=quote.bid,
                ask=quote.ask,
                last=quote.last,
                ts=as_of,
                session_id=quote.instrument.canonical(),
            )
        )
    return events


def _instruments_and_masters(
    chain: ChainFixture, as_of_date: date
) -> tuple[list[InstrumentKey], list[InstrumentMaster]]:
    instruments = [chain.underlying] + [quote.instrument for quote in chain.quotes]
    masters = [_master(instrument, as_of_date) for instrument in instruments]
    return instruments, masters


def _positions(chain: ChainFixture, trade_date: date) -> list[Position]:
    calls = [q.instrument for q in chain.quotes if q.instrument.option_right == "C"]
    return [
        Position(
            valuation_ts=_as_of(trade_date),
            portfolio_id="pf-recon",
            contract_key=call.canonical(),
            quantity=quantity,
            source="record",
        )
        for call, quantity in zip(calls[:3], [10.0, -5.0, 3.0], strict=True)
    ]


def _seed_raw(store: ParquetStore, chain: ChainFixture, trade_date: date) -> None:
    store.write("raw_market_events", _day_events(chain, trade_date))


_RECORDS_PER_POPULATED_DAY = 77


def test_a_missing_partition_is_flagged_explicitly_not_masked(tmp_path: Path) -> None:
    chain = get_fixture("synthetic_known_answer")
    store = ParquetStore(tmp_path / "store")
    d0 = date(2026, 3, 2)
    d1 = date(2026, 3, 3)
    d2 = date(2026, 3, 4)
    _seed_raw(store, chain, d0)
    _seed_raw(store, chain, d2)

    instruments, masters = _instruments_and_masters(chain, d0)
    report = reconstruct_range(
        store,
        d0,
        d2,
        _positions(chain, d0),
        instruments=instruments,
        masters=masters,
        config=_config(),
        config_hashes={"cfg": "cfg"},
        as_of_for=_as_of,
        calc_ts_for=_calc_ts,
    )

    assert report.missing_dates == (d1,)
    assert report.reconstructed_dates == (d0, d2)

    missing_day = report.day(d1)
    assert missing_day.status == MISSING
    assert missing_day.outputs is None
    assert missing_day.record_count == 0
    assert "no stored raw partition" in missing_day.reason

    assert store.read("iv_points", trade_date=d1, underlying="AAPL") == []
    assert store.read("market_state_snapshots", trade_date=d1, underlying="AAPL") == []
    assert (d1, "AAPL") not in store.list_partitions("iv_points")

    assert report.day(d0).status == RECONSTRUCTED
    assert report.day(d2).status == RECONSTRUCTED


def test_a_day_with_a_raw_partition_but_no_usable_quotes_is_empty_not_missing(
    tmp_path: Path,
) -> None:
    chain = get_fixture("synthetic_known_answer")
    store = ParquetStore(tmp_path / "store")
    trade_date = date(2026, 3, 5)
    as_of = _as_of(trade_date)
    bid_only = [
        event
        for event in _day_events(chain, trade_date)
        if event.field_name == "bid"
    ]
    store.write("raw_market_events", bid_only)

    instruments, masters = _instruments_and_masters(chain, trade_date)
    outcome = reconstruct_day(
        store,
        trade_date,
        [],
        instruments=instruments,
        masters=masters,
        config=_config(),
        config_hashes={"cfg": "cfg"},
        as_of=as_of,
        calc_ts=_calc_ts(trade_date),
    )

    assert outcome.status == EMPTY
    assert not outcome.is_missing
    assert outcome.outputs is not None and outcome.outputs.is_empty()
    assert outcome.record_count == 0


def test_a_multi_day_range_reconstructs_end_to_end(tmp_path: Path) -> None:
    chain = get_fixture("synthetic_known_answer")
    store = ParquetStore(tmp_path / "store")
    start = date(2026, 3, 2)
    days = [start + timedelta(days=offset) for offset in range(5)]
    for trade_date in days:
        _seed_raw(store, chain, trade_date)

    assert stored_trade_dates(store) == tuple(days)

    instruments, masters = _instruments_and_masters(chain, start)
    report = reconstruct_range(
        store,
        days[0],
        days[-1],
        _positions(chain, start),
        instruments=instruments,
        masters=masters,
        config=_config(),
        config_hashes={"cfg": "cfg"},
        as_of_for=_as_of,
        calc_ts_for=_calc_ts,
    )

    assert report.reconstructed_dates == tuple(days)
    assert report.missing_dates == ()
    assert [day.trade_date for day in report.days] == days
    for day in report.days:
        assert day.status == RECONSTRUCTED
        assert day.record_count == _RECORDS_PER_POPULATED_DAY

    for trade_date in days:
        iv_rows = store.read("iv_points", trade_date=trade_date, underlying="AAPL")
        assert len(iv_rows) == 10


def test_restated_outputs_write_to_versioned_partitions_old_survives(
    tmp_path: Path,
) -> None:
    chain = get_fixture("synthetic_known_answer")
    store = ParquetStore(tmp_path / "store")
    trade_date = date(2026, 3, 2)
    _seed_raw(store, chain, trade_date)
    instruments, masters = _instruments_and_masters(chain, trade_date)
    positions = _positions(chain, trade_date)
    as_of, calc_ts = _as_of(trade_date), _calc_ts(trade_date)

    v1 = reconstruct_day(
        store, trade_date, positions, instruments=instruments, masters=masters,
        config=_config(), config_hashes={"cfg": "cfg-v1"}, as_of=as_of, calc_ts=calc_ts, version="v1",
    )
    assert v1.status == RECONSTRUCTED

    v1_iv_before = sorted(
        store.read("iv_points", trade_date=trade_date, underlying="AAPL", version="v1"),
        key=lambda point: point.contract_key,
    )
    assert v1_iv_before

    v2 = reconstruct_day(
        store, trade_date, positions, instruments=instruments, masters=masters,
        config=_config(), config_hashes={"cfg": "cfg-v2"}, as_of=as_of, calc_ts=calc_ts, version="v2",
    )
    assert v2.status == RECONSTRUCTED

    assert store.list_versions("iv_points", trade_date, "AAPL") == ["v1", "v2"]

    v1_iv_after = sorted(
        store.read("iv_points", trade_date=trade_date, underlying="AAPL", version="v1"),
        key=lambda point: point.contract_key,
    )
    assert v1_iv_after == v1_iv_before

    v2_iv = store.read("iv_points", trade_date=trade_date, underlying="AAPL", version="v2")
    assert all(point.provenance.config_hashes == {"cfg": "cfg-v1"} for point in v1_iv_after)
    assert all(point.provenance.config_hashes == {"cfg": "cfg-v2"} for point in v2_iv)
    assert v2_iv, "v2 must have written iv points"


def test_replay_and_live_agree_on_overlapping_dates_same_code_version(
    tmp_path: Path,
) -> None:
    chain = get_fixture("synthetic_known_answer")
    store = ParquetStore(tmp_path / "store")
    trade_date = date(2026, 3, 2)
    _seed_raw(store, chain, trade_date)
    instruments, masters = _instruments_and_masters(chain, trade_date)
    positions = _positions(chain, trade_date)
    as_of, calc_ts = _as_of(trade_date), _calc_ts(trade_date)

    live = reconstruct_day(
        store, trade_date, positions, instruments=instruments, masters=masters,
        config=_config(), config_hashes={"cfg": "cfg"}, as_of=as_of, calc_ts=calc_ts, persist=True,
    )
    assert live.status == RECONSTRUCTED

    replay = reconstruct_day(
        store, trade_date, positions, instruments=instruments, masters=masters,
        config=_config(), config_hashes={"cfg": "cfg"}, as_of=as_of, calc_ts=calc_ts, persist=False,
    )
    assert replay.outputs is not None
    comparison = compare_replay_to_live(store, trade_date, replay.outputs)

    assert comparison.agrees
    assert comparison.divergent_tables == ()
    table_names = {table.table for table in comparison.tables}
    assert "risk_aggregates" in table_names
    for table in comparison.tables:
        assert table.replay_count == table.live_count
        assert table.replay_count > 0


def test_replay_vs_live_names_the_divergent_table_when_they_differ(
    tmp_path: Path,
) -> None:
    chain = get_fixture("synthetic_known_answer")
    store = ParquetStore(tmp_path / "store")
    trade_date = date(2026, 3, 2)
    _seed_raw(store, chain, trade_date)
    instruments, masters = _instruments_and_masters(chain, trade_date)
    positions = _positions(chain, trade_date)
    as_of, calc_ts = _as_of(trade_date), _calc_ts(trade_date)

    reconstruct_day(
        store, trade_date, positions, instruments=instruments, masters=masters,
        config=_config(), config_hashes={"cfg": "cfg-live"}, as_of=as_of, calc_ts=calc_ts, persist=True,
    )
    drifted = reconstruct_day(
        store, trade_date, positions, instruments=instruments, masters=masters,
        config=_config(), config_hashes={"cfg": "cfg-drift"}, as_of=as_of, calc_ts=calc_ts, persist=False,
    )
    assert drifted.outputs is not None
    comparison = compare_replay_to_live(store, trade_date, drifted.outputs)

    assert not comparison.agrees
    assert comparison.divergent_tables
    iv_agreement = next(t for t in comparison.tables if t.table == "iv_points")
    assert not iv_agreement.agrees
    assert iv_agreement.divergent_keys


def test_an_inverted_date_range_is_refused(tmp_path: Path) -> None:
    chain = get_fixture("synthetic_known_answer")
    store = ParquetStore(tmp_path / "store")
    with pytest.raises(ValueError, match="precedes start"):
        reconstruct_range(
            store,
            date(2026, 3, 4),
            date(2026, 3, 2),
            _positions(chain, date(2026, 3, 4)),
            instruments=_instruments_and_masters(chain, date(2026, 3, 4))[0],
            masters=_instruments_and_masters(chain, date(2026, 3, 4))[1],
            config=_config(),
            config_hashes={"cfg": "cfg"},
            as_of_for=_as_of,
            calc_ts_for=_calc_ts,
        )
