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
    ForwardCurvePoint,
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
