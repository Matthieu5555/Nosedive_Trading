# 0015 — Storage repository port + tiered backends (DuckDB query layer, SQLite metadata)

- **Status:** accepted (DuckDB + SQLite tier deferred until a concrete consumer exists — see note)
- **Date:** 2026-06-03
- **Source:** Vincent's ADR-020; merged 2026-06-05

## Context

The system needs to scale beyond two crypto underlyings to equity (hundreds of instruments per
underlying, multi-provider). Operator querying (range scans, cross-kind joins) against a raw
`glob + pyarrow` pattern is inadequate at equity cardinality. The stores (`RawStore`,
`DerivedStore`, `RiskStore`, `RunRegistry`) were concrete classes with no Protocol; one consumer
(`routers/risk.py`) read `store.root` directly to glob the disk — a leak that would break any
non-filesystem backend.

The blueprint already prescribes the target: one relational metadata store and one object/
partitioned-file store (Part I), with four retention tiers (Part XV). The constraint is: nothing
breaks during the migration.

## Decision

1. **Extract a repository Protocol per store** (`RawEventRepository`, `DerivedRepository`,
   `RiskRepository`, `RunRepository`, …), exposing only the typed methods already consumed —
   `write_events`/`read_events`/`trade_dates`, `read_surface`/`surface_versions`, etc. — over
   typed dataclass rows, never Arrow tables or filesystem paths. Current concrete classes become
   `ParquetRawStore`, etc., satisfying the Protocol structurally. Fix the `root` leak:
   add `list_portfolios()`/`list_valuations()` to the port so routers never glob the disk.

2. **Tier-precise backends, not "move everything to a single DB":**

   | Tier | Backend | Rationale |
   |---|---|---|
   | Raw events (immutable) | Parquet (stays) | Anchor for byte-identical replay; `decimal128(38,n)` round-trips exactly; columnar append-only scan-by-date. A mutable row-store would break determinism. |
   | Derived (snapshots/IV/surface/risk/triage) | DuckDB query layer over existing Parquet | DuckDB reads Parquet natively — zero migration. Embedded, zero server, vectorized, preserves Decimal. Enables operator range-scans and cross-kind joins. |
   | Metadata/lineage (`RunRegistry`) | SQLite behind `RunRepository` | Point-lookups, tiny, referential. Current JSON-per-file is the worst fit; SQLite is the cleaner match. |
   | Operational state (positions/orders/fills) | Postgres, later, in `execution` | Transactional/concurrent; out of `infra` scope (ADR-011/012). |

3. **DuckDB is additive (read layer only); it changes nothing about how data is written.** The
   raw bytes written today are what DuckDB reads. `decimal128(38,6)` and `(38,12)` round-trip
   to Python `Decimal` exactly via Arrow (`fetch_arrow_table().to_pylist()`).

4. **Provider dimension (ADR 0017) is a write-path partition segment** and must be added early
   (before equity data is written at scale). The DuckDB query layer and SQLite metadata are
   additive and can be retrofitted at zero migration cost — build them when a concrete consumer
   exists.

## Note — DuckDB + SQLite tier deferred (2026-06-04)

The Protocol extraction (§1) and the `root` leak fix are delivered. The DuckDB query layer (§2
derived tier) and SQLite `RunRepository` (§2 metadata tier) are deferred until a concrete consumer
exists — front-v2 history range views, backtest result analysis, or equity-scale reconstruction.
Building §2 now would be speculative infra ahead of need. The DuckDB conformance proof is banked
(branch `feat/phase-e-db`); retrofit is a cherry-pick plus a few tests.

## Consequences

Analytics modules never import `storage` — the swap is purely at the contract level. The `root`
leak is closed. Raw Parquet remains the replay anchor; DuckDB is a query convenience, not a new
source of truth. Exit cost: low (DuckDB is additive; SQLite is one file).
