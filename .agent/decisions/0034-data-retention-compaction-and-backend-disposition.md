# 0034 — Data retention tiers, cold-compaction, and backend disposition

- **Status:** accepted, 2026-06-07 (owner-confirmed in design review).
  **§3 cold-compaction for `daily_bar` landed 2026-06-15** (branch
  `agent-a2da7533e14eb5ceb`): `TableSpec.cold_compactable` flag, `storage.compaction`
  module, `ParquetStore` hot+cold union read, `scripts/compact_daily_bar.py` one-shot
  migration, 17 gate-green tests.
- **Date:** 2026-06-07.
- **Implements:** blueprint **Part XV** (data governance: retention tiers, lineage) and **Step 4(f)**
  ("decide retention policy"). Completes the storage-architecture decision set for the index pipeline.
- **Relates to:** [[0015-storage-repository-port-tiered-backends]], [[0017-provider-dimension]],
  [[0019-one-immutable-raw-model]], [[0028-configuration-and-reproducibility-standard]],
  [[0033-analytical-storage-duckdb-polars-over-parquet]], [[0011-blueprint-as-plan-of-record]].

## Context

The data stack is largely decided: Parquet is the immutable record (0019); DuckDB (`ASOF JOIN`) +
Polars over Parquet, behind the `StorageRepository` port, is the query/compute layer (0033);
`provider` is a first-class field + partition segment (0017); per-bundle `config_hashes`, manifest
freeze, and as-of config profiles give reproducibility (0028). A blueprint check (2026-06-06) confirmed
the blueprint **mandates** "columnar partitioned by trade date, underlying, and data layer" and the
four-tier retention scheme, but **names no specific database** — Postgres/SQLite/DuckDB are all our
choice. A web-research audit (2026-06-06) confirmed that for a **single-host, one-writer, EOD-batch**
shop, `Parquet + DuckDB + SQLite` is a complete stack and Postgres is not required.

Three gaps remained unaddressed by any ADR. This ADR closes them so the data architecture is fixed.

## Decision

### 1. Backend disposition — PostgreSQL is optional and unused in the core deployment

The core single-server deployment runs on **Parquet (raw + derived) + DuckDB (query) + SQLite
(metadata: runs, config profiles)**. **PostgreSQL is not required** and is not on the build path. The
existing `PostgresRunRepository` (behind the `RunRepository` port, gated on the `psycopg` extra) is
**kept as an optional, unused backend** — reserved for a future need that genuinely forces it:
multi-host concurrent writes, or the transactional operational state of **execution** (Phase 3 orders/
fills, which live in a separate `execution` module, not `infra`). No new code is built on Postgres
until such a consumer exists. This is the ports paying off (0015): the swap is configuration, not a
rewrite.

### 2. Retention — the blueprint's four tiers are policy now (enforcement is deferred)

Per blueprint Part XV, retention is tiered by fidelity/value:

| Tier | Data | Retention intent |
|------|------|------------------|
| 1 | Raw events (`raw_market_events`, `instrument_master`) | Long — supports replay/forensics; the evidentiary record |
| 2 | Normalized snapshots (`market_state_snapshots`) | ≥ raw if storage forces a compromise (compact, analytically rich) |
| 3 | Derived analytics (forwards, iv, surfaces, risk, scenarios, projections, daily bars) | Long-horizon trend + audit |
| 4 | Summary reports + **manifests** | Indefinite (small, operationally load-bearing) |

Retention is **policy, not yet enforcement** — nothing deletes data today, and at the current/near
scale nothing should. A retention/pruning job is built **only when storage pressure is measured**, and
it must honor the immutability + lineage rules (no silent mutation; a deletion is a recorded action).

### 3. Scale lever — cold-compaction *by ticker*, not a live-layout change

The physical layout stays `…/<layer>/<table>/[provider=<P>/]trade_date=<D>/underlying=<SYM>[/version=<V>]`
(date × underlying, blueprint-literal, per-ticker atomic recompute). Its only scale cost is the
data-lake **small-files problem** for low-row derived tables at **SP500 × many years** (millions of
tiny files → slow listing/footer-reads/backup). At SX5E scale (~50 names) this is a non-issue.

The scale lever is **not** to change the live write layout but to **compact cold data by ticker**: a
background job that merges old `(trade_date, underlying)` files into coarser **`(underlying, month|year)`**
files — which *also* speeds the dominant front-page read (one ticker's history across dates). It runs
on cold partitions only, never touches the raw layer's immutability semantics, and is **built when a
measured threshold is crossed** (e.g. adding the SP500 universe, or file-count/query-latency past a set
bound), not speculatively (0015 discipline). The "one file per day, all tickers" alternative is
explicitly **rejected** as the lever: it deviates from blueprint-literal underlying-partitioning and is
worse for the by-ticker read pattern.

### 4. Partition key — `provider` is added; `code_version`/`config_hash` stay in the stamp, not the path

[[0017-provider-dimension]] specifies the partition key `(provider, underlying, trade_date,
code_version, config_hash)`. This ADR refines the *physical* expression of it:

- **`provider` IS a physical partition segment** — sources must never mix on disk (0017's core need).
  Target path: `…/<table>/provider=<P>/trade_date=<D>/underlying=<SYM>[/version=<V>]`.
- **`code_version` and `config_hash` are NOT physical partition directories.** Making them path
  segments fragments the tree on every code/config change. They already live, per record, in the
  `ProvenanceStamp` (`code_version`, per-bundle `config_hashes`) and in the run manifest (0028);
  restatement under new code/config uses the existing **`version=<V>`** segment (0019). This honors
  0017's intent — source-traceable, no accidental cross-source joins — without over-fragmenting.

`provider` as a partition segment is **not yet implemented** in `ParquetStore`/`partitioning.py`
(today's key is `trade_date × underlying [× version]`). 0017 requires it **before equity data is
written at scale**, so it is a foundational, pre-1C task — tracked in
[`tasks/D1-storage-foundation.md`](../../tasks/D1-storage-foundation.md).

### 5. Effective-dated reference (index membership) lives in Parquet, queried by `ASOF JOIN`

Point-in-time index membership (`IndexConstituent`) is an **append-only Parquet table** in a
`reference` layer, carrying **effective time** (`effective_add_date`/`effective_remove_date`) **and**
knowledge/as-of time, resolved by DuckDB **`ASOF JOIN`** (0033) — uniform with all other as-of
resolution, and survivorship-bias-free by construction (retain every name ever a member; gate joins
with `check-lookahead-bias`). It is **not** a separate SQLite store. SQLite stays for the two
content-addressed/point-lookup metadata stores that are not time-series joins: the **run registry** and
the **config-profile store** (0028).

## Alternatives considered (rejected)

- **Adopt PostgreSQL now** — no current requirement forces it (single host, one writer, EOD batch);
  it is operational weight the ports let us add later if execution/multi-host ever needs it.
- **"One file per day, all tickers" as the live derived layout** — deviates from blueprint-literal
  underlying-partitioning and is worse for the by-ticker read pattern; cold-compaction-by-ticker is the
  better lever.
- **`code_version`/`config_hash` as physical partition dirs** — over-fragments the tree; they belong in
  the provenance stamp + manifest, with `version=<V>` for restatement.
- **A SQLite membership store** — breaks the uniform DuckDB-`ASOF`-over-Parquet model (0033) for no
  gain at this data's tiny size.

## Consequences

- The data architecture is **fixed**: Parquet (record) + DuckDB/Polars (query) + SQLite (metadata),
  no Postgres in core; retention is policy; cold-compaction-by-ticker is the documented scale lever,
  measured-trigger only; `provider` is the partition segment to implement before equity scale.
- One foundational implementation task falls out (`provider` partitioning) — see
  [`tasks/D1-storage-foundation.md`](../../tasks/D1-storage-foundation.md). Retention enforcement and
  cold-compaction are **carried-forward, build-when-measured** items, not current work.
- Phase tables (`DailyBar`, `IndexConstituent`, `ProjectedOptionAnalytics`, gated `FuturesPoint`) are
  specified per phase as their phase opens (board convention), against this architecture.
