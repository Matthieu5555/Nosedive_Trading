# M1 — Storage and the raw/curated layer

- **Branch:** `feat/merge-storage`
- **Owns:** `packages/infra/src/algotrading/infra/storage/**` (+ its README).
- **Depends on:** M0 (the `StorageRepository` port + frozen contracts/schema).
- **Blocks:** M4 (raw capture/replay), M7 (orchestration persistence), indirectly all derived writers.

## Objective

Produce one storage layer that is the union of both builds: our immutable Parquet-over-DuckDB curated layer with versioned restatement, behind Vincent's repository-port abstraction and his tiered/EAV stores. This is where Vincent is genuinely deeper than us, so the default is *adopt his structure, keep our versioning semantics*.

## What to merge

- **Keep ours:** the versioned-read semantics we fixed (`version=None` reads live/unversioned rows only; `version=<V>` reads exactly that restatement; the two never mix; versioned writes refused on append-only tables — `VersionedWriteNotAllowed`). This was a real double-count bug fix on the reconstruct-beside-live path. See our `backend/src/storage/{adapter,partitioning}.py` + `test_storage.py`. Also keep our immutable-raw + all-or-nothing staged-write discipline and schema-evolution-enforced-on-read.
- **Adopt from Vincent** (`packages/infra/src/algotrading/infra/storage/`): the `ports.py` repository abstraction (the seam M0 froze), the **EAV raw-event model** (`events.py`), `derived.py` + `query/duckdb_derived.py` for the curated reads, and the **tiered stores** — `runs.py`/`sqlite_runs.py` (run registry), `positions_store.py`, `risk_store.py`, `triage_store.py`, `universe_store.py`, `metrics.py`, `json_io.py`, `_partition_keys.py`.
- **Reconcile:** his EAV raw layer vs our immutable-Parquet raw layer. The repository port must expose both an append-only raw write and the versioned derived write; one immutable raw source of truth, recomputable downstream. Land one `schema.py` (merged with M0's frozen schema), not two.

## Frozen seam

Implement the `StorageRepository` port frozen by M0. Everyone writes/reads through it — no module reaches into Parquet or DuckDB directly. Publish the partition layout + version semantics in the storage README.

## Test surface

Read [TESTING.md] first. Specific to M1:
- Port conformance: every store passes the `StorageRepository` contract test (adopt Vincent's `test_ports_conformance.py`).
- The versioning invariant (carry ours over): a restated/replayed analytic writes to a versioned partition and the older partition **survives alongside** the new — assert coexistence and that `version=None` still reproduces the original layout byte-for-byte.
- Append-only raw cannot be versioned-written (`VersionedWriteNotAllowed`); raw is immutable once written.
- Round-trip each tiered store (runs/positions/risk/triage/universe) and the EAV raw events; golden-bytes test on a stored partition (adopt Vincent's `test_golden_storage_bytes.py`) so byte-identical replay (M7) has a stable substrate.

## Done criteria

One storage package behind the port, our versioning semantics preserved under Vincent's tiered/EAV structure, gate green, golden-bytes stable. M4 can capture raw and replay from it; M7 can persist runs/derived/risk through it.

## Gotchas

Do not let two raw models coexist — pick one immutable raw representation and make the EAV/Parquet choice once, explicitly, in an ADR. The versioning semantics are a *bug fix*, not a preference; if Vincent's layer doesn't have them, they go in, not the other way. Keep the port narrow — if a caller needs DuckDB SQL, that lives behind a derived-query method, not leaked upward.
