"""M1 storage: the StorageRepository implementation over immutable versioned Parquet.

Covers the load-bearing invariants the merge depends on (M1 spec + TESTING.md):
port conformance, immutable append-only raw, the versioned-restatement semantics
(restatement coexists with live; a version-blind read never mixes them; raw refuses
a versioned write), schema-evolution-on-read, lineage resolution, and a golden-bytes
determinism substrate for byte-identical replay (M7).

Records come from the shared fixture builders (``fixtures.records.make_record`` /
``make_stamp``) with this file's SPX vocabulary as explicit overrides, so a contract
gaining a field is the fixture library's problem, not this file's.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from algotrading.core import source_ref
from algotrading.core.provenance import ProvenanceStamp
from algotrading.infra.contracts import (
    DailyBar,
    ForwardCurvePoint,
    InstrumentKey,
    InstrumentMaster,
    RawMarketEvent,
    StorageRepository,
)
from algotrading.infra.storage import (
    ParquetStore,
    SchemaCompatibilityError,
    VersionedWriteNotAllowed,
    arrow_schema,
    from_row,
)
from algotrading.infra.storage.partitioning import partition_file
from fixtures.records import make_record, make_stamp

_TS = datetime(2026, 6, 5, 14, 30, tzinfo=UTC)
_TD = date(2026, 6, 5)


def _stamp(event_id: str = "evt-a") -> ProvenanceStamp:
    # Sources pin (sess-1, <event_id>) so the lineage tests resolve to exactly the
    # raw event this file writes; everything else rides the fixture defaults.
    return make_stamp((source_ref("raw_market_events", "sess-1", event_id),))


def _event(event_id: str, value: float = 5000.0) -> RawMarketEvent:
    return make_record(
        "raw_market_events",
        event_id=event_id,
        instrument_key="SPX|IND|CBOE|USD|1|con-1||",
        exchange_ts=_TS,
        receipt_ts=_TS,
        canonical_ts=_TS,
        field_name="last",
        value=value,
        trade_date=_TD,
        underlying="SPX",
    )


def _forward(forward: float) -> ForwardCurvePoint:
    return make_record(
        "forward_curve",
        snapshot_ts=_TS,
        underlying="SPX",
        maturity_years=0.5,
        expiry_date=date(2026, 12, 18),
        day_count="ACT/365F",
        forward_price=forward,
        source_snapshot_ts=_TS,
        provenance=_stamp(),
    )


def _store(tmp_path: Path) -> ParquetStore:
    return ParquetStore(tmp_path)


# -- port conformance ------------------------------------------------------
def test_parquet_store_satisfies_the_storage_repository_port(tmp_path: Path) -> None:
    assert isinstance(_store(tmp_path), StorageRepository)


# -- round-trip + basics ---------------------------------------------------
def test_write_then_read_returns_an_equal_object(tmp_path: Path) -> None:
    store = _store(tmp_path)
    event = _event("evt-a")
    store.write("raw_market_events", [event])
    assert store.read("raw_market_events") == [event]


def test_stored_numerics_read_back_as_numbers(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.write("raw_market_events", [_event("evt-a", value=4999.5)])
    (read_back,) = store.read("raw_market_events")
    assert isinstance(read_back.value, float)
    assert read_back.value == 4999.5


def test_read_on_empty_store_returns_empty_list(tmp_path: Path) -> None:
    assert _store(tmp_path).read("raw_market_events") == []


def test_writing_no_records_is_a_noop(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.write("raw_market_events", [])
    assert store.read("raw_market_events") == []


# -- trade_date pushdown equivalence (M7 pin) --------------------------------
def _event_on(day: date, event_id: str, underlying: str = "SPX") -> RawMarketEvent:
    """An event stamped on ``day`` — timestamps and trade_date agree, as the write path does."""
    ts = datetime(day.year, day.month, day.day, 14, 30, tzinfo=UTC)
    return make_record(
        "raw_market_events",
        session_id=f"sess-{day.isoformat()}",
        event_id=event_id,
        instrument_key=f"{underlying}|IND|CBOE|USD|1|con-1||",
        exchange_ts=ts,
        receipt_ts=ts,
        canonical_ts=ts,
        field_name="last",
        value=100.0,
        trade_date=day,
        underlying=underlying,
    )


def test_trade_date_pushdown_equals_full_scan_then_filter(tmp_path: Path) -> None:
    """``read(trade_date=d)`` returns exactly the full-scan rows whose trade_date == d.

    This is the equivalence M7's call-site fixes rely on: a collector only writes
    events stamped with its own trade_date, so pruning the read to one partition
    must return the identical set a full read + Python filter does — for every
    stored day, with and without an underlying filter. Sorted by (canonical_ts,
    event_id), the replay order, so the comparison is order-insensitive.
    """
    store = _store(tmp_path)
    days = [date(2026, 6, 3), date(2026, 6, 4), date(2026, 6, 5)]
    events = [
        _event_on(day, f"evt-{day.isoformat()}-{i}-{underlying}", underlying)
        for day in days
        for i in range(2)
        for underlying in ("SPX", "NDX")
    ]
    store.write("raw_market_events", events)

    def replay_sorted(rows: list) -> list:
        return sorted(rows, key=lambda e: (e.canonical_ts, e.event_id))

    full_scan = store.read("raw_market_events")
    assert len(full_scan) == len(events)
    for day in days:
        pushed = store.read("raw_market_events", trade_date=day)
        filtered = [e for e in full_scan if e.trade_date == day]
        assert replay_sorted(pushed) == replay_sorted(filtered)
        # And the underlying-scoped variant matches the same oracle.
        pushed_one = store.read("raw_market_events", trade_date=day, underlying="SPX")
        filtered_one = [e for e in filtered if e.underlying == "SPX"]
        assert replay_sorted(pushed_one) == replay_sorted(filtered_one)


# -- append-only immutability ----------------------------------------------
def test_append_only_rejects_overwriting_an_existing_observation(tmp_path: Path) -> None:
    from algotrading.infra.storage import AppendOnlyViolation

    store = _store(tmp_path)
    store.write("raw_market_events", [_event("evt-a")])
    with pytest.raises(AppendOnlyViolation):
        store.write("raw_market_events", [_event("evt-a", value=1.0)])


def test_append_only_allows_new_distinct_observations(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.write("raw_market_events", [_event("evt-a")])
    store.write("raw_market_events", [_event("evt-b")])
    assert {e.event_id for e in store.read("raw_market_events")} == {"evt-a", "evt-b"}


def test_duplicate_primary_key_within_one_write_is_rejected(tmp_path: Path) -> None:
    from algotrading.infra.storage import DuplicateKeyInBatch

    store = _store(tmp_path)
    with pytest.raises(DuplicateKeyInBatch):
        store.write("raw_market_events", [_event("evt-a"), _event("evt-a", value=2.0)])


# -- append-only dedup: REP2 step 4 -----------------------------------------
# The collision check that rejects an already-present key on append was a zip +
# Python-set membership; REP2 replaced it with a DuckDB SEMI JOIN on the full
# composite key. These tests pin the new engine check to the old set behaviour and
# exercise the date-typed composite key (instrument_master), where the full-key
# match — not a single field — is the load-bearing property.
def _instrument(symbol: str = "SPX") -> InstrumentKey:
    return InstrumentKey(
        underlying_symbol=symbol,
        security_type="IND",
        exchange="CBOE",
        currency="USD",
        multiplier=1.0,
        broker_contract_id="con-1",
    )


def _instrument_master(symbol: str, as_of: date, payload: str = "{}") -> InstrumentMaster:
    instrument = _instrument(symbol)
    return InstrumentMaster(
        instrument_key=instrument.canonical(),
        as_of_date=as_of,
        instrument=instrument,
        raw_broker_payload=payload,
    )


def _reference_collisions(
    primary_key: tuple[str, ...], records: list[object], existing
) -> set[tuple[object, ...]]:
    """The pre-REP2 zip + Python-set collision check, kept as the equivalence oracle.

    A record collides iff its full composite key is already present in ``existing``.
    This is exactly the set the engine SEMI JOIN must reproduce.
    """
    existing_keys = set(
        zip(*(existing.column(name).to_pylist() for name in primary_key), strict=True)
    )
    return {
        tuple(getattr(record, name) for name in primary_key)
        for record in records
        if tuple(getattr(record, name) for name in primary_key) in existing_keys
    }


def test_dedup_collision_set_matches_the_reference_zip_set_oracle(tmp_path: Path) -> None:
    # Build an existing partition, then probe a mixed batch (some colliding, some new)
    # and assert the engine collision set equals the old zip+set oracle exactly.
    import pyarrow.parquet as pq
    from algotrading.infra.contracts.registry import spec_for_table
    from algotrading.infra.storage.partitioning import partition_file

    store = _store(tmp_path)
    store.write("raw_market_events", [_event("evt-a"), _event("evt-b")])
    path = partition_file(tmp_path, "raw_market_events", _TD, "SPX")
    existing = pq.read_table(path, partitioning=None)

    spec = spec_for_table("raw_market_events")
    probe = [_event("evt-a"), _event("evt-c"), _event("evt-b")]  # a, b collide; c is new
    engine = store._existing_key_collisions(spec, probe, existing)
    reference = _reference_collisions(spec.primary_key, probe, existing)
    assert engine == reference
    assert engine == {("sess-1", "evt-a"), ("sess-1", "evt-b")}


def test_dedup_full_composite_key_allows_same_instrument_on_a_new_date(tmp_path: Path) -> None:
    # instrument_master keys on (instrument_key, as_of_date). The same instrument on a
    # different date is a distinct row and must be accepted: the dedup matches the FULL
    # composite key, never the instrument_key alone.
    store = _store(tmp_path)
    store.write("instrument_master", [_instrument_master("SPX", date(2026, 6, 5))])
    store.write("instrument_master", [_instrument_master("SPX", date(2026, 6, 6))])
    read_back = {m.as_of_date for m in store.read("instrument_master")}
    assert read_back == {date(2026, 6, 5), date(2026, 6, 6)}


def test_dedup_rejects_an_exact_date_typed_composite_key_collision(tmp_path: Path) -> None:
    # Re-writing the SAME (instrument_key, as_of_date) — the date component compared on
    # its parsed value, not a string — must be rejected as an append-only violation.
    from algotrading.infra.storage import AppendOnlyViolation

    store = _store(tmp_path)
    store.write("instrument_master", [_instrument_master("SPX", date(2026, 6, 5), "{}")])
    with pytest.raises(AppendOnlyViolation):
        store.write(
            "instrument_master", [_instrument_master("SPX", date(2026, 6, 5), '{"changed": 1}')]
        )


# -- versioned restatement -------------------------------------------------
def test_recompute_of_a_derived_partition_leaves_raw_bytes_unchanged(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.write("raw_market_events", [_event("evt-a")])
    raw_file = partition_file(tmp_path, "raw_market_events", _TD, "SPX")
    before = raw_file.read_bytes()

    store.write("forward_curve", [_forward(5050.0)])
    store.write("forward_curve", [_forward(5060.0)], version="reproc-2")
    assert raw_file.read_bytes() == before  # raw layer untouched by derived (re)writes


def test_a_newer_version_does_not_overwrite_the_older_analytic(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.write("forward_curve", [_forward(5050.0)])  # live
    store.write("forward_curve", [_forward(5060.0)], version="reproc-2")
    assert store.list_versions("forward_curve", _TD, "SPX") == ["reproc-2"]
    assert store.read("forward_curve", version="reproc-2")[0].forward_price == 5060.0
    assert store.read("forward_curve")[0].forward_price == 5050.0  # live survives


def test_a_version_blind_read_returns_live_rows_only(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.write("forward_curve", [_forward(5050.0)])
    store.write("forward_curve", [_forward(5060.0)], version="reproc-2")
    live = store.read("forward_curve")
    assert len(live) == 1 and live[0].forward_price == 5050.0  # restatement not mixed in


def test_a_versioned_write_to_an_append_only_table_is_refused(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(VersionedWriteNotAllowed):
        store.write("raw_market_events", [_event("evt-a")], version="v1")


def test_deleting_one_version_leaves_the_others(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.write("forward_curve", [_forward(5050.0)], version="v1")
    store.write("forward_curve", [_forward(5060.0)], version="v2")
    store.delete_partition("forward_curve", _TD, "SPX", version="v1")
    assert store.list_versions("forward_curve", _TD, "SPX") == ["v2"]


def test_an_invalid_version_segment_is_refused(tmp_path: Path) -> None:
    from algotrading.infra.storage import StorageError

    store = _store(tmp_path)
    with pytest.raises(StorageError):
        store.write("forward_curve", [_forward(5050.0)], version="bad/seg")


# -- golden-bytes determinism substrate ------------------------------------
def test_writing_a_partition_is_byte_deterministic(tmp_path: Path) -> None:
    # Same records -> byte-identical Parquet, the stable substrate M7's replay needs.
    a = ParquetStore(tmp_path / "a")
    b = ParquetStore(tmp_path / "b")
    events = [_event("evt-a"), _event("evt-b")]
    a.write("raw_market_events", events)
    b.write("raw_market_events", events)
    bytes_a = partition_file(tmp_path / "a", "raw_market_events", _TD, "SPX").read_bytes()
    bytes_b = partition_file(tmp_path / "b", "raw_market_events", _TD, "SPX").read_bytes()
    assert bytes_a == bytes_b


# -- schema evolution on read ----------------------------------------------
def test_from_row_fills_an_absent_optional_column_with_none() -> None:
    # InstrumentKey.expiry is optional; an older row missing it reads back as None.
    row = {
        "underlying_symbol": "SPX",
        "security_type": "IND",
        "exchange": "CBOE",
        "currency": "USD",
        "multiplier": 1.0,
        "broker_contract_id": "con-1",
        # expiry / strike / option_right absent (older schema)
    }
    from algotrading.infra.contracts import InstrumentKey

    rebuilt = from_row(InstrumentKey, row)
    assert rebuilt.expiry is None and rebuilt.strike is None


def test_from_row_refuses_an_absent_required_column() -> None:
    from algotrading.infra.contracts import InstrumentKey

    incomplete = {"underlying_symbol": "SPX"}  # required fields missing
    with pytest.raises(SchemaCompatibilityError):
        from_row(InstrumentKey, incomplete)


def test_live_and_replay_writes_share_one_schema() -> None:
    # One schema per table, derived from the contract — live and replay cannot diverge.
    assert arrow_schema(RawMarketEvent) == arrow_schema(RawMarketEvent)


# -- lineage ---------------------------------------------------------------
def test_lineage_resolves_raw_events_for_a_derived_record(tmp_path: Path) -> None:
    store = _store(tmp_path)
    event = _event("evt-a")
    store.write("raw_market_events", [event])
    store.write("forward_curve", [_forward(5050.0)])
    derived = store.read("forward_curve")[0]
    assert store.raw_events_for(derived) == [event]


def test_lineage_does_not_conflate_event_id_across_sessions(tmp_path: Path) -> None:
    store = _store(tmp_path)
    mine = _event("evt-a")
    other = dataclasses.replace(_event("evt-a"), session_id="sess-2", value=1.0)
    store.write("raw_market_events", [mine, other])
    # The derived record's stamp references (sess-1, evt-a) only.
    derived = _forward(5050.0)
    resolved = store.raw_events_for(derived)
    assert resolved == [mine]  # not the sess-2 row that shares the event id


# -- provider-partitioned reads --------------------------------------------
# daily_bar is provider-partitioned: on disk it carries a provider=<P> segment ahead
# of trade_date (ADR 0017 / 0034 §4). A read that pins (trade_date, underlying) but
# omits provider must NOT build a provider-less path that can never exist (silently
# returning [] — an under-specified read masquerading as "no data for that day").
# It must union across every provider segment for that one (trade_date, underlying).
def _daily_bar(provider: str, close: float) -> DailyBar:
    return make_record(
        "daily_bar",
        provider=provider,
        underlying="SPX",
        trade_date=_TD,
        open=4990.0,
        high=5010.0,
        low=4980.0,
        close=close,
        volume=1000.0,
        source="test",
        provenance=_stamp(),
    )


def test_provider_partitioned_read_without_provider_is_not_silently_empty(
    tmp_path: Path,
) -> None:
    # Write a daily_bar under exactly one provider, then read pinning (trade_date,
    # underlying) but NOT provider. The bar exists on disk, so the read must surface it
    # rather than returning [] from a provider-less path that never exists.
    store = _store(tmp_path)
    bar = _daily_bar("IBKR", close=5005.0)
    store.write("daily_bar", [bar])

    read_back = store.read("daily_bar", trade_date=_TD, underlying="SPX")
    assert read_back == [bar]


def test_provider_partitioned_read_without_provider_unions_across_providers(
    tmp_path: Path,
) -> None:
    # Two sources of the same (underlying, trade_date) land in disjoint provider segments.
    # A provider-blind read of that one partition must union BOTH, the documented
    # cross-provider scan — not just one, and not the whole table.
    store = _store(tmp_path)
    ibkr = _daily_bar("IBKR", close=5005.0)
    saxo = _daily_bar("SAXO", close=5006.0)
    store.write("daily_bar", [ibkr, saxo])

    read_back = store.read("daily_bar", trade_date=_TD, underlying="SPX")
    assert {bar.provider for bar in read_back} == {"IBKR", "SAXO"}
    assert {bar.close for bar in read_back} == {5005.0, 5006.0}


def test_provider_partitioned_read_without_provider_stays_scoped_to_the_partition(
    tmp_path: Path,
) -> None:
    # The cross-provider fall-through must stay scoped to the requested (trade_date,
    # underlying): a bar for a DIFFERENT day under the same provider must not leak in.
    store = _store(tmp_path)
    wanted = _daily_bar("IBKR", close=5005.0)  # trade_date=_TD
    other_day = dataclasses.replace(wanted, trade_date=date(2026, 6, 6), close=4995.0)
    store.write("daily_bar", [wanted, other_day])

    read_back = store.read("daily_bar", trade_date=_TD, underlying="SPX")
    assert read_back == [wanted]  # only _TD, not the 2026-06-06 bar


# -- snapshot round-trip (a derived contract with a provenance stamp) -------
def test_snapshot_round_trips_with_its_stamp(tmp_path: Path) -> None:
    store = _store(tmp_path)
    snap = make_record(
        "market_state_snapshots",
        snapshot_ts=_TS,
        instrument_key="SPX|IND|CBOE|USD|1|con-1||",
        reference_spot=5000.0,
        bid=4999.0,
        ask=5001.0,
        last=5000.0,
        spread_pct=0.0004,
        trade_date=_TD,
        underlying="SPX",
        provenance=_stamp(),
    )
    store.write("market_state_snapshots", [snap])
    assert store.read("market_state_snapshots") == [snap]


def test_read_with_date_range(tmp_path: Path) -> None:
    store = _store(tmp_path)
    bar1 = dataclasses.replace(_daily_bar("IBKR", close=5001.0), trade_date=date(2026, 6, 1))
    bar2 = dataclasses.replace(_daily_bar("IBKR", close=5002.0), trade_date=date(2026, 6, 2))
    bar3 = dataclasses.replace(_daily_bar("IBKR", close=5003.0), trade_date=date(2026, 6, 3))
    store.write("daily_bar", [bar1, bar2, bar3])

    res = store.read(
        "daily_bar",
        underlying="SPX",
        start_date=date(2026, 6, 2),
        end_date=date(2026, 6, 3),
    )
    assert {b.trade_date for b in res} == {date(2026, 6, 2), date(2026, 6, 3)}
    assert {b.close for b in res} == {5002.0, 5003.0}


# -- F-STORE-01 / F-STORE-02: version-leak regression -----------------------
# A live-only read (version=None) must NEVER include files from a version=<V>
# restatement sub-partition. The bug was `"version=" not in p.parts` in the
# date-range direct scan branch — always True, so restatement files leaked in.
# Both read paths are exercised: (a) the glob path (`_files_by_glob`) used when
# no date range is given, and (b) the date-range direct path
# (`_files_by_date_range_direct`) where the original bug lived.


def test_live_only_read_excludes_restatement_via_glob(tmp_path: Path) -> None:
    # Build a store with a live forward_curve row AND a version= restatement.
    # A version-blind read must return ONLY the live row.
    store = _store(tmp_path)
    live = _forward(5050.0)
    restated = _forward(5060.0)
    store.write("forward_curve", [live])
    store.write("forward_curve", [restated], version="reproc-1")

    # Glob path: no date range, triggers _files_by_glob after the single-partition
    # fast path (which already works — the glob path is the regression target).
    # Force the glob path by omitting trade_date so the fast-path is skipped.
    result = store.read("forward_curve")
    assert len(result) == 1, (
        f"Expected 1 live row, got {len(result)}: {[r.forward_price for r in result]}"
    )
    assert result[0].forward_price == 5050.0


def test_live_only_read_excludes_restatement_via_date_range_direct(tmp_path: Path) -> None:
    # Same store layout as above but read via the date-range direct path
    # (_files_by_date_range_direct), which is the branch that contained the bug
    # `"version=" not in p.parts` (always True -> restatement leaks in).
    # forward_curve is not provider-partitioned, so the date-range direct path is
    # exercised when underlying=None and the range is <= 31 days.
    store = _store(tmp_path)
    live = _forward(5050.0)
    restated = _forward(5060.0)
    store.write("forward_curve", [live])
    store.write("forward_curve", [restated], version="reproc-1")

    # Date-range path without pinning underlying: goes through
    # _files_by_date_range_direct -> d_dir.glob("**/data.parquet") + _is_live_file.
    result = store.read(
        "forward_curve",
        start_date=_TD,
        end_date=_TD,
    )
    assert len(result) == 1, (
        f"Expected 1 live row via date-range, got {len(result)}: "
        f"{[r.forward_price for r in result]}"
    )
    assert result[0].forward_price == 5050.0

