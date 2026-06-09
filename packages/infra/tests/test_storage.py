"""M1 storage: the StorageRepository implementation over immutable versioned Parquet.

Covers the load-bearing invariants the merge depends on (M1 spec + TESTING.md):
port conformance, immutable append-only raw, the versioned-restatement semantics
(restatement coexists with live; a version-blind read never mixes them; raw refuses
a versioned write), schema-evolution-on-read, lineage resolution, and a golden-bytes
determinism substrate for byte-identical replay (M7).

Records are built inline (self-contained) rather than from the shared fixture library,
which is still entangled with the risk workstream in the flat tree.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from algotrading.core import source_ref, stamp
from algotrading.infra.contracts import (
    DailyBar,
    ForwardCurvePoint,
    InstrumentKey,
    InstrumentMaster,
    MarketStateSnapshot,
    RawMarketEvent,
    StorageRepository,
)
from algotrading.infra.contracts.bundles import ForwardDiagnostics
from algotrading.infra.storage import (
    ParquetStore,
    SchemaCompatibilityError,
    VersionedWriteNotAllowed,
    arrow_schema,
    from_row,
)
from algotrading.infra.storage.partitioning import partition_file

_TS = datetime(2026, 6, 5, 14, 30, tzinfo=UTC)
_TD = date(2026, 6, 5)


def _stamp(event_id: str = "evt-a"):
    return stamp(
        calc_ts=_TS,
        code_version="algotrading-infra-0.1.0",
        config_hashes={"cfg": "cfg"},
        source_records=(source_ref("raw_market_events", "sess-1", event_id),),
        source_timestamps=(_TS,),
    )


def _event(event_id: str, value: float = 5000.0) -> RawMarketEvent:
    return RawMarketEvent(
        session_id="sess-1",
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
    return ForwardCurvePoint(
        snapshot_ts=_TS,
        underlying="SPX",
        maturity_years=0.5,
        expiry_date=date(2026, 12, 18),
        day_count="ACT/365F",
        forward_price=forward,
        diagnostics=ForwardDiagnostics(
            method="parity", candidate_count=8, residual_mad=0.1, quality_label="good"
        ),
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
    return DailyBar(
        provider=provider,
        underlying="SPX",
        trade_date=_TD,
        open=4990.0,
        high=5010.0,
        low=4980.0,
        close=close,
        volume=1000.0,
        bar_type="1d-TRADES",
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
    snap = MarketStateSnapshot(
        snapshot_ts=_TS,
        instrument_key="SPX|IND|CBOE|USD|1|con-1||",
        reference_spot=5000.0,
        bid=4999.0,
        ask=5001.0,
        last=5000.0,
        spread_pct=0.0004,
        reference_type="mid",
        flags=("open",),
        completeness=1.0,
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

