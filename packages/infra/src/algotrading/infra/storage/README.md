# infra.storage

The storage layer. Two tiers, distinct backends, distinct guarantees — see ADR 0015
(tiered backends) and ADR 0019 (one immutable raw model).

## Analytics data plane (M1) — immutable raw + versioned derived, over Parquet

`ParquetStore` (`adapter.py`) is the implementation of the frozen
`algotrading.infra.contracts.StorageRepository` port. Everything reads and writes
through that port — no module reaches into Parquet or DuckDB directly.

- **Layout:** `<root>/<layer>/<table>/trade_date=<YYYY-MM-DD>/underlying=<SYM>[/version=<V>]/data.parquet`
  (`partitioning.py`). Layer/keys come from the contract registry.
- **Immutable raw (append-only):** raw events and the instrument master are written
  once and never changed; re-writing an existing primary key raises `AppendOnlyViolation`.
  This is the byte-identical-replay anchor.
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
- **Lineage:** `source_records_for` / `raw_events_for` resolve a derived record's
  provenance stamp back to the exact source rows by full primary key.

## Metadata / serving tier (M10) — run registry, over SQLite / Postgres

`RunRepository` (`ports.py`) with `SqliteRunRepository` (local) and
`PostgresRunRepository` (deployed), selected by `factory.make_run_repository()`. Small,
relational, point-looked-up — the blueprint's "relational metadata store" (Part I),
orthogonal to the analytics data-plane port. See ADR 0015.
