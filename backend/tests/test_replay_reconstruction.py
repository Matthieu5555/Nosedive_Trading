"""Historical reconstruction and replay over a date range (Workstream E, step 13).

These are behavior tests for the batch layer that sits on top of the actor's proven
same-code-path replay (the lead's headline test owns the byte-identity proof; this
file owns the date-range orchestration around it). The named cases come straight from
the spec's "Orchestration and replay robustness" test surface:

* a missing raw partition is flagged explicitly, never masked by silent interpolation;
* a restatement writes to a versioned partition and a newer-code run does not
  overwrite the older analytic — the old version survives alongside the new and reads
  back its own values;
* at least one multi-day range reconstructs end to end (a compressed range, said so
  out loud — TESTING.md forbids silent truncation);
* replay and live agree on overlapping dates under the same code version.

Inputs are built from the named ``synthetic_known_answer`` chain fixture, reusing the
exact (events, instruments, masters) construction the byte-identical test uses, so the
days under test bind to one curated fixture home rather than inline literals. Expected
outcomes are derived independently here: which dates are seeded vs. left empty, how
many derived records a populated day yields, and that two restatements under different
config hashes carry different stamp values.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from config import (
    PlatformConfig,
    QcThresholdConfig,
    ScenarioConfig,
    SolverConfig,
    UniverseConfig,
)
from contracts import InstrumentMaster, Position, RawMarketEvent
from contracts.instrument_key import InstrumentKey
from fixtures.events import quote_events
from fixtures.library import ChainFixture, get_fixture
from orchestration.reconstruction import (
    EMPTY,
    MISSING,
    RECONSTRUCTED,
    compare_replay_to_live,
    reconstruct_day,
    reconstruct_range,
    stored_trade_dates,
)
from storage import ParquetStore


def _config() -> PlatformConfig:
    return PlatformConfig(
        universe=UniverseConfig(version="u-1", underlyings=("AAPL",), exchange="SMART"),
        qc_threshold=QcThresholdConfig(
            version="qc-1", max_spread_pct=0.5, max_quote_age_seconds=30.0, min_chain_count=1
        ),
        solver=SolverConfig(version="iv-1", iv_tolerance=1e-12, max_iterations=200),
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
    """The injected market-snapshot instant for a trade date (15:30 UTC)."""
    return datetime(trade_date.year, trade_date.month, trade_date.day, 15, 30, tzinfo=UTC)


def _calc_ts(trade_date: date) -> datetime:
    """The injected computation instant for a trade date (16:00 UTC)."""
    return datetime(trade_date.year, trade_date.month, trade_date.day, 16, 0, tzinfo=UTC)


def _day_events(chain: ChainFixture, trade_date: date) -> list[RawMarketEvent]:
    """The chain fixture as one day's raw events, stamped on ``trade_date``.

    Mirrors the byte-identical test's construction: per-instrument session ids keep
    ``(session_id, event_id)`` unique so the events are a valid append-only raw batch,
    and every event is stamped at the day's as-of instant so it partitions on that
    trade date. Same chain, different day, is exactly how a multi-day range is seeded.
    """
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
    """Write one day's chain events to the immutable raw layer."""
    store.write("raw_market_events", _day_events(chain, trade_date))


# Independently-derived expected record count for one populated synthetic_known_answer
# day, read off the byte-identical test's asserted output and confirmed by probing the
# actor: 11 snapshots, 1 forward, 10 IV points, 1 surface params, 5 grid cells, 3
# pricings, 1 risk aggregate, 18 scenarios = 50 derived records.
_RECORDS_PER_POPULATED_DAY = 50


def test_a_missing_partition_is_flagged_explicitly_not_masked(tmp_path: Path) -> None:
    # Seed two days and deliberately leave the middle day with no raw partition.
    # The range [d0, d2] must report d1 as MISSING — a named, hard outcome — and must
    # NOT fabricate an empty ActorOutputs for it (that would be silent interpolation
    # of a gap, the exact failure mode this case forbids).
    chain = get_fixture("synthetic_known_answer")
    store = ParquetStore(tmp_path / "store")
    d0 = date(2026, 3, 2)
    d1 = date(2026, 3, 3)  # intentionally not seeded — the gap
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
        config_hash="cfg",
        as_of_for=_as_of,
        calc_ts_for=_calc_ts,
    )

    # The gap day is named explicitly as missing, the two seeded days reconstructed.
    assert report.missing_dates == (d1,)
    assert report.reconstructed_dates == (d0, d2)

    # No fabricated output for the missing day: status MISSING, outputs is None.
    missing_day = report.day(d1)
    assert missing_day.status == MISSING
    assert missing_day.outputs is None
    assert missing_day.record_count == 0
    assert "no stored raw partition" in missing_day.reason

    # The missing day left nothing on disk — not even an empty derived partition.
    # (A storage read scoped to one (trade_date, underlying) returns only that
    # partition; an unscoped/date-only read unions every date, so we scope both.)
    assert store.read("iv_points", trade_date=d1, underlying="AAPL") == []
    assert store.read("market_state_snapshots", trade_date=d1, underlying="AAPL") == []
    assert (d1, "AAPL") not in store.list_partitions("iv_points")

    # And the surrounding days are real reconstructions, so the gap is a gap, not a
    # blanket failure that would also "explain away" the missing day.
    assert report.day(d0).status == RECONSTRUCTED
    assert report.day(d2).status == RECONSTRUCTED


def test_a_day_with_a_raw_partition_but_no_usable_quotes_is_empty_not_missing(
    tmp_path: Path,
) -> None:
    # EMPTY and MISSING are different facts and the report keeps them apart. Seed a day
    # whose only raw events are one-sided bids (no mid, no last fallback) so snapshots
    # are unusable and the actor produces nothing — that is EMPTY (partition present,
    # no derived rows), never MISSING (no partition at all). No positions: with no
    # usable market state the valuation join has nothing to value, and the actor
    # returns an empty result rather than a partial object only when there is no risk
    # tuple to resolve, so the empty-day case is positions-free by construction.
    chain = get_fixture("synthetic_known_answer")
    store = ParquetStore(tmp_path / "store")
    trade_date = date(2026, 3, 5)
    as_of = _as_of(trade_date)
    # One-sided bid only: insufficient for a usable snapshot (see fixtures.events
    # single_bid_event rationale) — so no forwards, IV, surfaces, pricing or risk.
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
        [],  # no positions: an empty market day with nothing to value
        instruments=instruments,
        masters=masters,
        config=_config(),
        config_hash="cfg",
        as_of=as_of,
        calc_ts=_calc_ts(trade_date),
    )

    assert outcome.status == EMPTY
    assert not outcome.is_missing
    assert outcome.outputs is not None and outcome.outputs.is_empty()
    assert outcome.record_count == 0


def test_a_multi_day_range_reconstructs_end_to_end(tmp_path: Path) -> None:
    # The spec asks for "at least one historical month"; this is a COMPRESSED stand-in
    # — five consecutive seeded trade dates — and that compression is stated out loud
    # here rather than silently sampling a longer span (TESTING.md forbids silent
    # truncation). The shape under test is identical to a month: a contiguous range
    # reconstructs every day in order, each to a full set of derived records.
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
        config_hash="cfg",
        as_of_for=_as_of,
        calc_ts_for=_calc_ts,
    )

    # Every day reconstructed, in ascending date order, none missing.
    assert report.reconstructed_dates == tuple(days)
    assert report.missing_dates == ()
    assert [day.trade_date for day in report.days] == days
    for day in report.days:
        assert day.status == RECONSTRUCTED
        assert day.record_count == _RECORDS_PER_POPULATED_DAY

    # End to end: each day's derived rows actually landed on disk for that date.
    for trade_date in days:
        iv_rows = store.read("iv_points", trade_date=trade_date, underlying="AAPL")
        assert len(iv_rows) == 10  # the populated-day IV count, per the chain fixture


def test_restated_outputs_write_to_versioned_partitions_old_survives(
    tmp_path: Path,
) -> None:
    # The headline restatement guarantee: a newer-code run writes under a version and
    # does NOT overwrite the older analytic. We write the original analytic under
    # version "v1", then restate the same day under "v2" with a different config hash
    # (the stand-in for a code/analytics change that moves the stamps and numbers).
    # After the restatement: both versions are present (list_versions has v1 AND v2),
    # and v1 reads back its OWN original values, untouched by the v2 write.
    chain = get_fixture("synthetic_known_answer")
    store = ParquetStore(tmp_path / "store")
    trade_date = date(2026, 3, 2)
    _seed_raw(store, chain, trade_date)
    instruments, masters = _instruments_and_masters(chain, trade_date)
    positions = _positions(chain, trade_date)
    as_of, calc_ts = _as_of(trade_date), _calc_ts(trade_date)

    v1 = reconstruct_day(
        store, trade_date, positions, instruments=instruments, masters=masters,
        config=_config(), config_hash="cfg-v1", as_of=as_of, calc_ts=calc_ts, version="v1",
    )
    assert v1.status == RECONSTRUCTED

    # Capture v1's persisted iv_points before the restatement, to prove they survive.
    v1_iv_before = sorted(
        store.read("iv_points", trade_date=trade_date, underlying="AAPL", version="v1"),
        key=lambda point: point.contract_key,
    )
    assert v1_iv_before  # the version actually wrote rows

    # Newer code: same day, restated under v2 with a different config hash.
    v2 = reconstruct_day(
        store, trade_date, positions, instruments=instruments, masters=masters,
        config=_config(), config_hash="cfg-v2", as_of=as_of, calc_ts=calc_ts, version="v2",
    )
    assert v2.status == RECONSTRUCTED

    # Both versions coexist for the partition.
    assert store.list_versions("iv_points", trade_date, "AAPL") == ["v1", "v2"]

    # v1 reads back byte-for-byte its original values — the v2 write did not touch it.
    v1_iv_after = sorted(
        store.read("iv_points", trade_date=trade_date, underlying="AAPL", version="v1"),
        key=lambda point: point.contract_key,
    )
    assert v1_iv_after == v1_iv_before

    # And the two versions are genuinely distinct analytics, not the same rows twice:
    # the differing config hash rode into every v2 stamp, so v1 and v2 disagree there.
    v2_iv = store.read("iv_points", trade_date=trade_date, underlying="AAPL", version="v2")
    assert {point.provenance.config_hash for point in v1_iv_after} == {"cfg-v1"}
    assert {point.provenance.config_hash for point in v2_iv} == {"cfg-v2"}


def test_replay_and_live_agree_on_overlapping_dates_same_code_version(
    tmp_path: Path,
) -> None:
    # The determinism guarantee, checked: a day that already ran live and persisted its
    # outputs, reconstructed again under the SAME code version, must agree on every
    # derived table. compare_replay_to_live measures it per table by primary key and
    # full value; under one version agreement must be total. (The helper exists to
    # catch a FUTURE drift, so the assertion is the whole point — not a smoke check.)
    chain = get_fixture("synthetic_known_answer")
    store = ParquetStore(tmp_path / "store")
    trade_date = date(2026, 3, 2)
    _seed_raw(store, chain, trade_date)
    instruments, masters = _instruments_and_masters(chain, trade_date)
    positions = _positions(chain, trade_date)
    as_of, calc_ts = _as_of(trade_date), _calc_ts(trade_date)

    # Live: run and persist (unversioned, replace-in-place — the live layout).
    live = reconstruct_day(
        store, trade_date, positions, instruments=instruments, masters=masters,
        config=_config(), config_hash="cfg", as_of=as_of, calc_ts=calc_ts, persist=True,
    )
    assert live.status == RECONSTRUCTED

    # Replay: reconstruct the same day under the same code version, compute only.
    replay = reconstruct_day(
        store, trade_date, positions, instruments=instruments, masters=masters,
        config=_config(), config_hash="cfg", as_of=as_of, calc_ts=calc_ts, persist=False,
    )
    assert replay.outputs is not None
    comparison = compare_replay_to_live(store, trade_date, replay.outputs)

    # Total agreement across every derived table, none divergent.
    assert comparison.agrees
    assert comparison.divergent_tables == ()
    # And the comparison was not vacuous: every table actually had rows on both sides,
    # including the portfolio-level risk aggregate that partitions under "_all".
    table_names = {table.table for table in comparison.tables}
    assert "risk_aggregates" in table_names
    for table in comparison.tables:
        assert table.replay_count == table.live_count
        assert table.replay_count > 0


def test_replay_vs_live_names_the_divergent_table_when_they_differ(
    tmp_path: Path,
) -> None:
    # The helper's reason for existing is to catch drift, so it must actually flag a
    # divergence, not just confirm agreement. Persist live under one config hash, then
    # compare a reconstruction computed under a DIFFERENT config hash: the stamps
    # differ, so every table diverges and the helper must name them with their keys.
    chain = get_fixture("synthetic_known_answer")
    store = ParquetStore(tmp_path / "store")
    trade_date = date(2026, 3, 2)
    _seed_raw(store, chain, trade_date)
    instruments, masters = _instruments_and_masters(chain, trade_date)
    positions = _positions(chain, trade_date)
    as_of, calc_ts = _as_of(trade_date), _calc_ts(trade_date)

    reconstruct_day(
        store, trade_date, positions, instruments=instruments, masters=masters,
        config=_config(), config_hash="cfg-live", as_of=as_of, calc_ts=calc_ts, persist=True,
    )
    drifted = reconstruct_day(
        store, trade_date, positions, instruments=instruments, masters=masters,
        config=_config(), config_hash="cfg-drift", as_of=as_of, calc_ts=calc_ts, persist=False,
    )
    assert drifted.outputs is not None
    comparison = compare_replay_to_live(store, trade_date, drifted.outputs)

    assert not comparison.agrees
    assert comparison.divergent_tables  # at least one table named
    # A named divergence points at specific rows, never a bare "they differ".
    iv_agreement = next(t for t in comparison.tables if t.table == "iv_points")
    assert not iv_agreement.agrees
    assert iv_agreement.divergent_keys


def test_an_inverted_date_range_is_refused(tmp_path: Path) -> None:
    # An end before the start is a caller bug; the driver surfaces it loudly rather
    # than silently reconstructing nothing (which would read as "no data" — the very
    # masking this workstream forbids).
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
            config_hash="cfg",
            as_of_for=_as_of,
            calc_ts_for=_calc_ts,
        )
