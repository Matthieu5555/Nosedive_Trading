# storage

DuckDB-over-Parquet persistence for the typed contracts. One `ParquetStore` owns a
data root and reads/writes every table family through `contracts`. Reads and
lineage go through DuckDB querying the Parquet files; writes go through pyarrow.

## On-disk layout

One Parquet file per partition, in a Hive-style tree:

```
<root>/<layer>/<table>/trade_date=<YYYY-MM-DD>/underlying=<SYM>/data.parquet
```

The layer comes from the table registry (`raw`, `snapshot`, `derived`,
`portfolio`, `qc`). The trade date and underlying are read off the record:
most tables carry them directly, the rest derive the trade date from their primary
timestamp (`snapshot_ts` / `valuation_ts` / `canonical_ts` / `run_ts`) and the
underlying from the first field of the instrument/contract key. A record that
carries none of these cannot be placed, and that raises rather than landing in a
catch-all (`partitioning.trade_date_of`).

There is exactly one Arrow schema per table and it is derived from the contract's
type hints (`schema.arrow_schema`), so a partition written live and one written in
replay are identical by construction — not by convention. (`test_storage.py::
test_live_and_replay_writes_land_in_identical_schemas`.)

Note the partition columns (`trade_date`, `underlying`) are *also* stored as real
columns inside each file. Reads therefore take the in-file copy and never let the
engine re-infer them from the directory names: DuckDB reads with
`hive_partitioning=false`, and the append-only re-read uses pyarrow
`partitioning=None`. This matters — re-inferring `trade_date` from the path yields
a `dictionary<string>` column that collides with the file's real `date32` one.

## Write semantics

- **Write-ahead validation.** Every record is validated (`contracts.validation`)
  before a single byte is written; a malformed record is rejected at the door with
  an explicit `ContractValidationError`, never coerced. That includes the
  provenance stamp: its wellformedness is checked by `provenance.validate_stamp`, so
  a stamp whose hash no longer matches its contents is refused.
- **All-or-nothing batches.** A `write` validates every record and rejects internal
  duplicate keys, then *prepares* every touched partition — including the
  append-only collision check — before committing any of them. Each partition is
  written to a temporary file and renamed into place only once all are staged. So a
  collision or a write failure in a later partition leaves every earlier one
  unchanged instead of half-writing the batch. (`test_a_failed_append_only_batch_
  leaves_every_partition_unchanged`, `test_a_write_that_fails_partway_commits_
  nothing`.) The one case not claimed is a process crash *between* the final
  renames.
- **Append-only layers** (`raw_market_events`, `instrument_master`): writing a row
  whose primary key already exists on disk is refused with `AppendOnlyViolation`.
  Raw observations are immutable once written. (`test_append_only_*`.)
- **Recompute-friendly derived layers**: writing a partition replaces just that
  partition's file. Recomputing or deleting one derived partition never rewrites
  the raw layer or any other partition. (`test_recomputing_a_derived_partition_
  leaves_the_raw_layer_byte_unchanged`, `test_delete_partition_isolates_to_that_
  partition`.)

## Schema evolution and backfill compatibility

The data is append-mostly and partitions are written over months, so old
partitions must stay readable as the contracts grow. The rules, and what enforces
each:

1. **Additive, nullable only — and enforced on read.** A new field is added to the
   *end* of a contract and must be `Optional`. A partition written before it existed
   reads back with that field as `None`, the rest of the row intact
   (`test_from_row_fills_an_absent_optional_column_with_none`). This is enforced in
   code, not just documented: `serialization.from_row` defaults an absent-or-null
   value to `None` *only* for an `Optional` field. A *required* field that comes back
   absent or null is refused with `SchemaCompatibilityError` rather than used to
   build an invalid instance — e.g. an `IvPoint` with `k=None` when `k` is a required
   float (`test_reading_a_partition_missing_a_required_column_is_refused`,
   `test_from_row_refuses_an_absent_required_column`). Reads use DuckDB
   `union_by_name`, so a column present in some partitions is simply null in the ones
   that predate it.
2. **No in-place removal or rename.** Removing or renaming a column silently changes
   the meaning of historical partitions. A rename is a new (nullable) column plus a
   one-off backfill, not an edit to the existing one.
3. **No in-place type change.** The Arrow type of a column is fixed for the life of
   the table. A different type is a new column or a deliberate, reviewed rewrite of
   the affected partitions — never an implicit coercion on read.
4. **A primary-key change is a new table.** The key set defines the partition and
   the dedup identity; changing it is a migration to a new table family, not an
   evolution of this one.
5. **Nested bundles evolve as JSON.** The instrument key, provenance stamp, and
   diagnostic bundles are stored as single JSON columns. Adding an optional field to
   one of those bundles is backward-compatible (old JSON simply lacks the key);
   removing or renaming one is not, and follows rules 2–3.

Any change beyond rule 1 is a contract change — and contracts are owned by
Workstream A and routed through it, never edited in place by a consumer.

## Reading and lineage

`read(table, trade_date=?, underlying=?)` returns contract instances (optionally
scoped to one partition).

Lineage reads the typed source references off a record's provenance stamp
(`provenance.SourceRecordRef`) and resolves each by its *full* primary key — so a
reference to one raw event never pulls back another that merely shares one key
field, e.g. a different session with the same event id. `source_records_for(record)`
is the general form: it returns the matching source records grouped by table, for
any source table, not only raw events. `raw_events_for(record)` is the
raw-market-events slice of that — the headline "which raw records produced this?"
question. (`test_lineage_resolves_raw_records_for_a_derived_object`,
`test_lineage_does_not_conflate_the_same_event_id_across_sessions`,
`test_source_records_for_resolves_a_non_raw_source_by_full_key`.)
