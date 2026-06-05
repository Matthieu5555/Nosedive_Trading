# storage

DuckDB-over-Parquet persistence for the typed contracts. One `ParquetStore` owns a
data root and reads/writes every table family through `contracts`. Reads and
lineage go through DuckDB querying the Parquet files; writes go through pyarrow.

This is the layer that enforces two of the platform's four invariants on disk: the
immutable raw layer (append-only tables refuse to overwrite an observation) and
determinism (live and replay write byte-identical schemas, derived from the
contract, so a partition written today and one recomputed in replay are the same
shape by construction rather than by convention). Validation and provenance are
checked at the write door, so malformed or tampered data never lands.

## Public interface

Construct one `ParquetStore(root)` over a data root and use it for everything. The
root is environment, not economics — the orchestration layer owns it and passes it
in; it is never read from `config`.

- `write(table, records, *, version=None)` — validate and persist a batch, all or
  nothing.
- `read(table, *, trade_date=None, underlying=None, version=None)` — read records
  back into contract instances, optionally scoped to one partition.
- `list_partitions(table)` — the `(trade_date, underlying)` partitions present.
- `list_versions(table, trade_date, underlying)` — the restatement versions
  present for one partition.
- `delete_partition(table, trade_date, underlying, version=None)` — drop one
  partition (or one version of it); idempotent.
- `source_records_for(record)` / `raw_events_for(record)` — lineage: the source
  records that produced a derived record, grouped by table (or just its raw
  events).

Module-level helpers and types are also re-exported: `primary_key_of`,
`arrow_schema`, `to_row` / `from_row`, and the error types `StorageError`,
`AppendOnlyViolation`, `DuplicateKeyInBatch`, `VersionedWriteNotAllowed`,
`SchemaCompatibilityError`.

## Fastest way to exercise it

```python
from pathlib import Path
from storage import ParquetStore
from fixtures.records import baseline_records

store = ParquetStore(Path("/tmp/store"))
records = baseline_records()
store.write("iv_points", [records["iv_points"]])
print(store.read("iv_points"))                 # one IvPoint, round-tripped
print(store.raw_events_for(records["iv_points"]))  # [] until raw events are written
```

From `backend/`, every guarantee below is pinned by a test in
`tests/test_storage.py`; run `uv run pytest -q tests/test_storage.py`.

## On-disk layout

One Parquet file per partition, in a Hive-style tree:

```
<root>/<layer>/<table>/trade_date=<YYYY-MM-DD>/underlying=<SYM>/data.parquet
```

The layer comes from the table registry (`raw`, `snapshot`, `derived`,
`portfolio`, `qc`). The trade date and underlying are read off the record:
most tables carry them directly, the rest derive the trade date from their primary
timestamp (`snapshot_ts` / `valuation_ts` / `canonical_ts` / `run_ts`, falling
back to `as_of_date`) and the underlying from the first `|`-separated field of the
`instrument_key` / `contract_key`. The trade date is mandatory: a record from
which none can be derived cannot be placed and raises a `StorageError` rather than
landing in a catch-all (`partitioning.trade_date_of`). The underlying is softer —
a record with no explicit `underlying` and no key field falls back to the literal
`_all` segment rather than raising (`partitioning.underlying_of`).

An optional fourth segment versions a partition:

```
<root>/<layer>/<table>/trade_date=<YYYY-MM-DD>/underlying=<SYM>/version=<V>/data.parquet
```

`version` is **off by default** — `write(..., version=None)` (the live recompute
path) produces exactly the three-segment layout above and preserves the original
path layout and read/write behavior, so every partition written before versioning
existed is untouched. Live analytics are unversioned; a restatement passes an
explicit version so a replayed analytic written under newer code lands *beside* the
live one instead of overwriting it (roadmap step 13, "versioned partitions").

The live partition and a restatement of it therefore coexist on disk for the same
`(trade_date, underlying)`. Reads keep them apart so a default read is never
double-counted across overlapping primary keys:

- `read(..., version=None)` returns the **live (unversioned) rows only** — the safe
  default a caller almost always wants.
- `read(..., version=V)` returns **only that restatement's** rows.
- A partition that holds only restatements (no live write) has no live rows, so a
  version-blind read of it is empty; inspect restatements via `list_versions`.

Versioning is for derived analytics: a versioned write to an append-only table
(raw events, instrument master) is refused with `VersionedWriteNotAllowed`, because
a raw observation is immutable and has no restatement. `list_versions(table,
trade_date, underlying)` enumerates the versions present; `delete_partition(...,
version=V)` drops one version and leaves the rest. (`test_a_newer_version_does_not_
overwrite_the_older_analytic`, `test_a_version_blind_read_returns_live_rows_only_not_
restatements`, `test_unversioned_write_keeps_the_original_on_disk_layout`.)

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

## Partition management

`list_partitions(table)` enumerates the `(trade_date, underlying)` partitions on
disk; `list_versions(table, trade_date, underlying)` enumerates the restatement
versions present for one of them (empty when it is unversioned). `delete_partition`
is idempotent — deleting a missing partition is a no-op — and removes either the
whole `(trade_date, underlying)` partition (`version=None`, including any version
sub-partitions) or just one `version=<V>` sub-partition.

## Failure modes

Every storage failure names the table and the offending key, so a rejected write
tells an operator exactly what went wrong rather than "write failed". None of these
are retryable as-is: each is a caller or data bug whose fix is to correct the input,
not to retry.

| Raised | When | Caller does |
|--------|------|-------------|
| `ContractValidationError` | A record fails a field rule, or its provenance stamp is malformed/tampered, on the write-ahead check | Fix the record; the message names the field. |
| `DuplicateKeyInBatch` | One `write` call contains two records with the same primary key | Deduplicate the batch before writing. |
| `AppendOnlyViolation` | A write to `raw_market_events` or `instrument_master` collides with an existing on-disk key | Do not rewrite a raw observation; it is immutable. |
| `VersionedWriteNotAllowed` | A versioned write targets an append-only table | Versioning is for derived restatements only. |
| `SchemaCompatibilityError` | A read finds a required (non-`Optional`) contract field absent or null in storage | The stored data no longer matches the contract (a removed/renamed column or type drift); see schema-evolution rules below. |
| `StorageError` | A record has no derivable trade date (`partitioning.trade_date_of`) | Give the record a `trade_date` or a recognized primary timestamp. |

Because writes prepare every partition before committing any, a malformed record
or a collision anywhere in a batch leaves the store exactly as it was — the one gap
not claimed is a process crash *between* the final renames in a multi-partition
commit.

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

`read(table, trade_date=?, underlying=?, version=?)` returns contract instances
(optionally scoped to one partition). `version` defaults to `None`, the live
(unversioned) rows; pass an explicit version to read one restatement (see the
versioning section above).

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
