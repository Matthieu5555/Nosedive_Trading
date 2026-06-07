# 0033 — Analytical storage & query: DuckDB + Polars over the immutable Parquet store

- **Status:** accepted, 2026-06-06.
- **Date:** 2026-06-06.
- **Implements:** roadmap **1E / storage** and the as-of join needs of **1A / 1C / 1F** in
  [`documentation/roadmap-index-analytics.md`](../../documentation/roadmap-index-analytics.md); supports
  the point-in-time discipline of **OQ-3** (index membership).
- **Relates to:** [[0015-storage-repository-port-tiered-backends]] (the port this sits behind),
  [[0019-one-immutable-raw-model]] (Parquet stays the record), [[0011-blueprint-as-plan-of-record]].

## Context

Parquet is the immutable system of record ([[0019-one-immutable-raw-model]]); DuckDB is already in the
stack. The index pipeline needs to query **years of daily bars and option snapshots** with
**point-in-time (as-of) joins** — option snapshots against the right daily bar, membership resolved as
of the date being reconstructed — on a single node.

A web-sourced audit (deep-research, 2026-06-06) confirmed: **DuckDB has a native `ASOF JOIN`**
purpose-built for financial time-series alignment, and **DuckDB ↔ Polars interop is zero-copy** via
Arrow. PyArrow is the underlying Arrow layer both ride on, not a third query engine to choose.

## Decision

1. **DuckDB is the query engine over the Parquet store.** Use its native **`ASOF JOIN`** for all
   point-in-time alignment (snapshots vs daily bars, membership-as-of), gating every historical join
   with the **`check-lookahead-bias`** discipline.
2. **Polars is the in-process dataframe** for transforms; DuckDB↔Polars is zero-copy (Arrow).
   **PyArrow is a transitive dependency** (the interop substrate), not a separately-chosen engine.
3. This lives **behind the existing `StorageRepository` port** ([[0015-storage-repository-port-tiered-backends]]);
   Parquet remains the immutable record ([[0019-one-immutable-raw-model]]). DuckDB/Polars **read and
   compute over** it — they are not a new system of record.

## Consequences

- Dependencies: `duckdb`, `polars` (both already partly present); no change to the raw model.
- As-of joins are expressed in DuckDB SQL rather than hand-rolled `merge_asof`, which is both faster on
  columnar data and harder to get subtly wrong on point-in-time semantics.

## Alternatives considered (rejected)

- **pandas `merge_asof` as the primary join** — in-memory, slower on large columnar data, and easier to
  get point-in-time semantics wrong than an engine-native `ASOF JOIN`.
- **A dedicated time-series database** — overkill for a single node and breaks the
  immutable-Parquet-as-record model ([[0019-one-immutable-raw-model]]).
- **PyArrow as a third query engine** — it is the Arrow interop substrate beneath DuckDB and Polars,
  not a query layer to pick separately.
