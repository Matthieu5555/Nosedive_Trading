"""Storage adapter: round-trip, append-only, isolation, schema, and lineage.

These are the load-bearing tests for the platform's storage promises. Each one
names a specific guarantee from the spec and asserts the bound, not just that the
code runs.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from fixtures import baseline_records
from fixtures.records import make_stamp
from provenance import source_ref
from storage import (
    AppendOnlyViolation,
    DuplicateKeyInBatch,
    ParquetStore,
    SchemaCompatibilityError,
    arrow_schema,
    from_row,
)
from storage.partitioning import partition_file, trade_date_of, underlying_of
from storage.serialization import to_row

ALL_TABLES = sorted(baseline_records().keys())


@dataclass(frozen=True, slots=True)
class _EvolvedRow:
    """A stand-in contract that has gained a new nullable column (``note``).

    The real contracts carry no optional top-level fields yet, so the additive-
    nullable read path is exercised against this rather than by deleting a
    required column — which must now be *refused*, not silently coerced to None.
    """

    snapshot_ts: datetime
    contract_key: str
    note: str | None = None


@pytest.mark.parametrize("table", ALL_TABLES)
def test_write_then_read_returns_an_equal_object(table: str, tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    record = baseline_records()[table]
    store.write(table, [record])
    read_back = store.read(table)
    assert read_back == [record]


def test_stored_numerics_read_back_as_numbers_not_strings(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    store.write("iv_points", [baseline_records()["iv_points"]])
    record = store.read("iv_points")[0]
    assert isinstance(record.iv, float)
    assert isinstance(record.total_variance, float)
    assert isinstance(record.diagnostics.iterations, int)


def test_read_on_empty_store_returns_empty_list(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    assert store.read("raw_market_events") == []


def test_writing_no_records_is_a_noop(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    store.write("raw_market_events", [])
    assert store.read("raw_market_events") == []


def test_append_only_rejects_overwriting_an_existing_observation(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    event = baseline_records()["raw_market_events"]
    store.write("raw_market_events", [event])
    # Same primary key (session_id, event_id) but a different value: must be refused.
    collision = dataclasses.replace(event, value=999.0)
    with pytest.raises(AppendOnlyViolation):
        store.write("raw_market_events", [collision])
    # The original observation is untouched.
    assert store.read("raw_market_events") == [event]


def test_append_only_allows_new_distinct_observations(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    first = baseline_records()["raw_market_events"]
    second = dataclasses.replace(first, event_id="evt-2", field_name="ask", value=190.6)
    store.write("raw_market_events", [first])
    store.write("raw_market_events", [second])
    read_back = store.read("raw_market_events")
    assert {record.event_id for record in read_back} == {"evt-1", "evt-2"}


def test_duplicate_primary_key_within_one_write_is_rejected(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    event = baseline_records()["raw_market_events"]
    duplicate = dataclasses.replace(event, value=1.0)  # same PK
    with pytest.raises(DuplicateKeyInBatch):
        store.write("raw_market_events", [event, duplicate])


def test_recomputing_a_derived_partition_leaves_the_raw_layer_byte_unchanged(
    tmp_path: Path,
) -> None:
    store = ParquetStore(tmp_path)
    raw_event = baseline_records()["raw_market_events"]
    forward = baseline_records()["forward_curve"]
    store.write("raw_market_events", [raw_event])
    store.write("forward_curve", [forward])

    raw_file = partition_file(
        tmp_path, "raw_market_events", trade_date_of(raw_event), underlying_of(raw_event)
    )
    raw_bytes_before = raw_file.read_bytes()

    # Recompute the derived partition, then delete it: neither touches raw.
    store.write("forward_curve", [forward])
    store.delete_partition(
        "forward_curve", trade_date_of(forward), underlying_of(forward)
    )

    assert raw_file.read_bytes() == raw_bytes_before
    assert store.read("raw_market_events") == [raw_event]


def test_delete_partition_isolates_to_that_partition(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    aapl = baseline_records()["forward_curve"]
    msft = dataclasses.replace(aapl, underlying="MSFT")
    store.write("forward_curve", [aapl, msft])

    store.delete_partition("forward_curve", trade_date_of(aapl), "AAPL")

    remaining = store.read("forward_curve")
    assert [record.underlying for record in remaining] == ["MSFT"]


def test_live_and_replay_writes_land_in_identical_schemas(tmp_path: Path) -> None:
    live_root = tmp_path / "live"
    replay_root = tmp_path / "replay"
    record = baseline_records()["iv_points"]
    ParquetStore(live_root).write("iv_points", [record])
    ParquetStore(replay_root).write("iv_points", [record])

    live_file = partition_file(
        live_root, "iv_points", trade_date_of(record), underlying_of(record)
    )
    replay_file = partition_file(
        replay_root, "iv_points", trade_date_of(record), underlying_of(record)
    )
    assert pq.read_schema(live_file) == pq.read_schema(replay_file)
    # ...and both match the schema derived from the contract.
    from contracts import IvPoint

    assert pq.read_schema(live_file) == arrow_schema(IvPoint)


def test_from_row_fills_an_absent_optional_column_with_none() -> None:
    # The additive-nullable case: a row predating the new optional `note` column
    # reads back with the rest intact and `note` as None.
    row = {
        "snapshot_ts": datetime(2026, 5, 29, 15, 30, tzinfo=UTC),
        "contract_key": "AAPL|OPT",
    }
    rebuilt = from_row(_EvolvedRow, row)
    assert rebuilt == _EvolvedRow(
        snapshot_ts=datetime(2026, 5, 29, 15, 30, tzinfo=UTC),
        contract_key="AAPL|OPT",
        note=None,
    )


def test_from_row_refuses_an_absent_required_column() -> None:
    # The flip side of the rule: a missing *required* field is an incompatibility,
    # not a None to paper over.
    row = {"snapshot_ts": datetime(2026, 5, 29, 15, 30, tzinfo=UTC)}  # no contract_key
    with pytest.raises(SchemaCompatibilityError) as info:
        from_row(_EvolvedRow, row)
    assert info.value.field == "contract_key"


def test_reading_a_partition_missing_a_required_column_is_refused(tmp_path: Path) -> None:
    # End to end through the store: a partition that lost a required column (`k`)
    # must not read back as a half-built IvPoint with k=None — it is refused.
    store = ParquetStore(tmp_path)
    record = baseline_records()["iv_points"]
    row = to_row(type(record), record)
    del row["k"]  # an old/corrupt schema that lacked the required `k` column

    from contracts import IvPoint

    full_schema = arrow_schema(IvPoint)
    old_schema = full_schema.remove(full_schema.get_field_index("k"))
    path = partition_file(tmp_path, "iv_points", trade_date_of(record), underlying_of(record))
    path.parent.mkdir(parents=True, exist_ok=True)
    old_table = pa.table({name: [row[name]] for name in old_schema.names}, schema=old_schema)
    pq.write_table(old_table, path)

    with pytest.raises(SchemaCompatibilityError) as info:
        store.read("iv_points")
    assert info.value.field == "k"


def test_lineage_resolves_raw_records_for_a_derived_object(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    base_event = baseline_records()["raw_market_events"]
    # Four raw events; the surface was built from two of them.
    events = [
        dataclasses.replace(base_event, event_id=f"evt-{n}", value=float(n)) for n in (1, 2, 3, 4)
    ]
    store.write("raw_market_events", events)

    surface = dataclasses.replace(
        baseline_records()["surface_parameters"],
        provenance=make_stamp(
            source_records=(
                source_ref("raw_market_events", "sess-1", "evt-1"),
                source_ref("raw_market_events", "sess-1", "evt-3"),
            )
        ),
    )
    store.write("surface_parameters", [surface])

    lineage = store.raw_events_for(surface)
    assert {event.event_id for event in lineage} == {"evt-1", "evt-3"}


def test_lineage_does_not_conflate_the_same_event_id_across_sessions(tmp_path: Path) -> None:
    # Two raw events share event_id "evt-1" but live in different sessions, so they
    # are distinct observations. Lineage keyed only on event_id would return both;
    # keyed on the full (session_id, event_id) it returns exactly the one referenced.
    store = ParquetStore(tmp_path)
    base = baseline_records()["raw_market_events"]
    sess_one = base  # (sess-1, evt-1)
    sess_two = dataclasses.replace(base, session_id="sess-2", value=2.0)  # (sess-2, evt-1)
    store.write("raw_market_events", [sess_one, sess_two])

    surface = dataclasses.replace(
        baseline_records()["surface_parameters"],
        provenance=make_stamp(source_records=(source_ref("raw_market_events", "sess-1", "evt-1"),)),
    )
    store.write("surface_parameters", [surface])

    lineage = store.raw_events_for(surface)
    assert [(event.session_id, event.event_id) for event in lineage] == [("sess-1", "evt-1")]


def test_source_records_for_resolves_a_non_raw_source_by_full_key(tmp_path: Path) -> None:
    # The generic lineage path: a forward point whose source is a market-state
    # snapshot, keyed by the composite (snapshot_ts, instrument_key). Exercises a
    # timestamp key component, which the reference canonicalizes to a UTC string.
    store = ParquetStore(tmp_path)
    snapshot = baseline_records()["market_state_snapshots"]
    store.write("market_state_snapshots", [snapshot])

    forward = dataclasses.replace(
        baseline_records()["forward_curve"],
        provenance=make_stamp(
            source_records=(
                source_ref("market_state_snapshots", snapshot.snapshot_ts, snapshot.instrument_key),
            )
        ),
    )
    store.write("forward_curve", [forward])

    resolved = store.source_records_for(forward)
    assert list(resolved.keys()) == ["market_state_snapshots"]
    assert resolved["market_state_snapshots"] == [snapshot]
    # The raw-events view is empty: this lineage has no raw-event source.
    assert store.raw_events_for(forward) == []


def test_a_failed_append_only_batch_leaves_every_partition_unchanged(tmp_path: Path) -> None:
    # The reproduced bug: a batch touching two partitions where the second collides
    # must not leave the first already written. The collision is caught in the
    # prepare phase, before any partition is committed.
    store = ParquetStore(tmp_path)
    existing = baseline_records()["raw_market_events"]  # (sess-1, evt-1), underlying AAPL
    store.write("raw_market_events", [existing])

    new_partition = dataclasses.replace(existing, event_id="evt-2", underlying="MSFT")
    collision = dataclasses.replace(existing, value=999.0)  # same PK (sess-1, evt-1)

    with pytest.raises(AppendOnlyViolation):
        store.write("raw_market_events", [new_partition, collision])

    # The brand-new MSFT partition was never created, and AAPL is untouched.
    trade_date = trade_date_of(existing)
    assert store.read("raw_market_events", trade_date=trade_date, underlying="MSFT") == []
    assert store.read("raw_market_events") == [existing]


def test_a_write_that_fails_partway_commits_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A physical write failure on the second partition must leave the store exactly
    # as it was: staged writes are renamed into place only after all succeed.
    import storage.adapter as adapter_module

    store = ParquetStore(tmp_path)
    aapl = baseline_records()["forward_curve"]  # forward=191.0, underlying AAPL
    msft = dataclasses.replace(aapl, underlying="MSFT")
    store.write("forward_curve", [aapl, msft])  # two partitions, version 1

    aapl_v2 = dataclasses.replace(aapl, forward=200.0)
    msft_v2 = dataclasses.replace(msft, forward=300.0)

    real_write_table = adapter_module.pq.write_table
    calls = {"count": 0}

    def flaky_write_table(*args: object, **kwargs: object) -> None:
        calls["count"] += 1
        if calls["count"] == 2:
            raise OSError("disk full (injected)")
        real_write_table(*args, **kwargs)

    monkeypatch.setattr(adapter_module.pq, "write_table", flaky_write_table)

    with pytest.raises(OSError, match="injected"):
        store.write("forward_curve", [aapl_v2, msft_v2])

    # Neither partition advanced to version 2.
    forwards = {record.underlying: record.forward for record in store.read("forward_curve")}
    assert forwards == {"AAPL": 191.0, "MSFT": 191.0}
