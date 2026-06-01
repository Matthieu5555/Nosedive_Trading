"""Storage adapter: round-trip, append-only, isolation, schema, and lineage.

These are the load-bearing tests for the platform's storage promises. Each one
names a specific guarantee from the spec and asserts the bound, not just that the
code runs.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from fixtures import baseline_records
from fixtures.records import make_stamp
from storage import AppendOnlyViolation, DuplicateKeyInBatch, ParquetStore, arrow_schema
from storage.partitioning import partition_file, trade_date_of, underlying_of
from storage.serialization import to_row

ALL_TABLES = sorted(baseline_records().keys())


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


def test_old_partition_missing_a_new_nullable_column_stays_readable(tmp_path: Path) -> None:
    # Backfill compatibility: a partition written before a column existed reads
    # back with that column as None, the rest intact.
    store = ParquetStore(tmp_path)
    record = baseline_records()["iv_points"]
    row = to_row(type(record), record)
    del row["k"]  # simulate an older schema that lacked the `k` column

    from contracts import IvPoint

    full_schema = arrow_schema(IvPoint)
    old_schema = full_schema.remove(full_schema.get_field_index("k"))
    path = partition_file(tmp_path, "iv_points", trade_date_of(record), underlying_of(record))
    path.parent.mkdir(parents=True, exist_ok=True)
    old_table = pa.table({name: [row[name]] for name in old_schema.names}, schema=old_schema)
    pq.write_table(old_table, path)

    read_back = store.read("iv_points")[0]
    assert read_back.k is None
    assert read_back.iv == record.iv
    assert read_back.contract_key == record.contract_key


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
        provenance=make_stamp(source_record_ids=("evt-1", "evt-3")),
    )
    store.write("surface_parameters", [surface])

    lineage = store.raw_events_for(surface)
    assert {event.event_id for event in lineage} == {"evt-1", "evt-3"}
