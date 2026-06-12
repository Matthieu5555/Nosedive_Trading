# infra.storage

The storage layer. Two tiers, distinct backends, distinct guarantees — see ADR 0015
(tiered backends) and ADR 0019 (one immutable raw model).

## Analytics data plane (M1) — immutable raw + versioned derived, over Parquet

`ParquetStore` (`adapter.py`) is the implementation of the frozen
`algotrading.infra.contracts.StorageRepository` port. Everything reads and writes
through that port — no module reaches into Parquet or DuckDB directly.

- **Layout:** `<root>/<layer>/<table>/trade_date=<YYYY-MM-DD>/underlying=<SYM>[/version=<V>]/data.parquet`
  (`partitioning.py`). Layer/keys come from the contract registry. A
  **provider-partitioned** table (registry `provider_partitioned=True`, e.g. `daily_bar`)
  prepends a `provider=<P>` segment ahead of the trade date —
  `<table>/provider=<P>/trade_date=<D>/underlying=<SYM>[/version=<V>]` (ADR 0017 / 0034 §4)
  — so two sources of the same `(underlying, trade_date)` land in disjoint partitions and a
  scan that omits `provider` can never cross sources. `read`/`list_versions`/
  `delete_partition` take an optional `provider=` to scope to one source.
  `underlyings_present(table, provider=)` answers "which symbols have any partition" from
  the directory names alone (no Parquet opened) — the cheap one-pass presence scan a
  backfill's skip-if-present check needs (a full-table `read` per ticker over the daily-bar
  small-file blow-up was the ohlc-backfill stall).
- **Immutable raw (append-only):** raw events and the instrument master are written
  once and never changed; re-writing an existing primary key raises `AppendOnlyViolation`.
  This is the byte-identical-replay anchor.
- **Reference layer (bitemporal, append-only):** point-in-time index membership
  (`index_constituents`, WS 1A) lives in a `reference` layer, partitioned by index (the
  `underlying=<SYM>` segment) then `effective_add_date` (the `trade_date=<D>` segment) —
  `partitioning.py` derives both from the `IndexConstituent` fields. It is provider-agnostic
  (no `provider=` segment) and append-only: a vendor restatement is a new row under a later
  `knowledge_date`, never an in-place edit. Resolved by a DuckDB `ASOF JOIN` in
  `universe.membership.members` (ADR 0033 / 0034 §5).
- **Versioned derived (restatement):** `write(..., version=None)` is the live,
  replace-in-place layout; `write(..., version="<V>")` lands a restatement *beside* the
  live partition. A version-blind read (`version=None`) returns the live rows only — the
  two never mix, which is what stops a reconstruct-beside-live run from double-counting.
  A versioned write to an append-only table is refused (`VersionedWriteNotAllowed`).
- **All-or-nothing writes:** every record is validated and the whole batch is staged to
  temp files, then renamed into place — a mid-batch failure commits nothing.
- **Schema-evolution on read** (`serialization.py`, `schema.py`): one Arrow schema per
  table, derived from the contract's type hints (so live and replay land in identical
  schemas). A new column must be optional; a required column read back absent raises
  `SchemaCompatibilityError` rather than building an invalid contract instance.
  The row codec's structural work (nested dataclasses, tuples, the nested half of the
  additive-nullable rule) runs on pydantic `TypeAdapter`s; the persisted bytes are
  unchanged and pinned by `tests/test_contracts_plane_golden.py` — compact sorted-key
  JSON columns, nested datetimes normalized to UTC ISO-8601. The raw-event JSON
  sidecar (`json_io.py`) rides the same adapters with its bespoke `__dec__` Decimal
  wire wrapper kept explicit, its exact text pinned by the same golden suite.
- **Lineage:** `source_records_for` / `raw_events_for` resolve a derived record's
  provenance stamp back to the exact source rows by full primary key. For a
  provider-partitioned source (`daily_bar`) `provider` is part of that key, so lineage is
  provider-scoped by construction — a stamp pointing at one source's bar never resolves to
  another source's bar for the same `(underlying, trade_date)`.

## Metadata / serving tier (M10) — run registry + config profiles, over SQLite / Postgres

`RunRepository` and `ProfileRepository` (`ports.py`), each satisfied by one SQLAlchemy
Core repository (`sql_repositories.py`: `SqlRunRepository`, `SqlProfileRepository`).
The SQLite/Postgres choice is the engine URL built by `factory.make_run_repository()` /
`factory.make_profile_repository()` (`POSTGRES_URL` selects Postgres; requires
`uv sync --extra postgres`). `RunRegistry` (`runs.py`) stays as the JSON-file,
zero-dependency reference. Small, relational, point-looked-up — the blueprint's
"relational metadata store" (Part I), orthogonal to the analytics data-plane port.
See ADR 0015.

Two persisted formats are pinned (golden tests): the SQLite `ended_at` column is
ISO-8601 'T'-separated text (`datetime.isoformat()`), and every payload column is
sorted-key JSON (`json.dumps(payload, sort_keys=True)`) — byte-identical to the
pre-SQLAlchemy backends, so existing database files are adopted without migration.
The record types themselves (`RunRecord` in `runs.py`, `ProfileVersion` in
`profiles.py`) are pydantic dataclasses: `from_dict` is one validation call (nested
`Manifest` defaults come from the dataclass itself), while `to_dict` stays hand-written
because its bytes are the persisted shape.
