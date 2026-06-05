# M10 — Postgres for the serving/metadata tier (conditional, future)

- **Branch:** `feat/merge-postgres` (do not open until a trigger below fires)
- **Owns:** the metadata/serving stores behind M1's port — the run registry,
  positions/risk/triage/universe stores, and whatever read models the M8 frontend
  needs. A new `PostgresRepository` (or per-store Postgres backend) *behind the
  existing `StorageRepository` port*. No new storage seam; no caller changes.
- **Depends on:** M1 (the `StorageRepository` port must exist and every caller must
  already go through it), M8 (the frontend is what makes the question real).
- **Blocks:** nothing. This is an upgrade, not a foundation.

## The argument — read this before doing anything

We do **not** have "a database" to migrate. We have two storage concerns doing two
different jobs, and they want different tools. Conflating them is the mistake this
task exists to prevent.

**1. The analytics data plane — the big pile of numbers.** Market data, surfaces,
prices, risk. Written once per `(trade_date, underlying)`, then only ever read.
Lives as immutable Parquet partitions, queried by DuckDB. No server. This stays on
DuckDB-over-Parquet **permanently.** Postgres would be a regression here, for three
concrete reasons, not preference:

- *Byte-identical replay would break.* Our determinism guarantee (ADR 0004, the M7
  headline test) rests on the on-disk bytes being derivable from the contract, so a
  partition written live and one recomputed in replay are identical and diffable
  against a committed golden artifact. Postgres gives no stable on-disk image to
  diff. Move the data plane and that test becomes impossible to state, let alone pass.
- *Immutable raw + versioned restatement are filesystem-cheap.* "Write a restatement
  beside the live partition, never over it" is a directory placement
  (`version=<V>/`, ADR 0007). In Postgres it becomes version columns, partial
  indexes, and discipline enforced in SQL — more moving parts for a guarantee we get
  for free from files.
- *It's the wrong shape and zero-ops.* This is append-mostly columnar OLAP over
  months of partitions — exactly DuckDB's strength and exactly a row-store's
  weakness. Files mean nothing to run, back up, or keep alive. If we ever want SQL
  over this data, DuckDB can attach Postgres and query across both; we never have to
  move the source of truth to get relational access to it.

**2. The metadata/serving tier — the little notebook of "what happened."** The run
registry (which stage ran, when, ok/failed — `orchestration/run_state.py` today,
plus Vincent's `runs.py`/`sqlite_runs.py`, `positions_store`, `risk_store`,
`triage_store`, `universe_store` arriving in M1). This is tiny next to the data
plane, and it is genuinely relational, concurrently-written state. **This** is the
only place Postgres earns its keep — and only once something needs several readers
and writers touching the same status at once. The natural progression is:

> JSON-lines / files (today) → SQLite (M1, Vincent's stores) → Postgres (here, when triggered)

SQLite is the right answer for a single host and a single writer. Postgres becomes
the more *maintainable* answer — not the less — the moment that stops being true,
because a shared server beats hand-rolled file locking and cross-process
coordination. Before that moment, a Postgres server is overhead we maintain for no
benefit.

**The verdict this task encodes:** keep DuckDB-over-Parquet for the data plane
forever; adopt Postgres only for the metadata/serving tier, only behind the port,
and only when a trigger below fires. It is *both tools, each on its own job* — never
a wholesale switch.

## Triggers — do not start until at least one is true

Open this workstream only when you can point at one of these in reality, not in
anticipation:

1. **The M8 frontend needs live concurrent reads of status** — the web app shows run
   state / positions / triage while a job is writing them, and SQLite's single-writer
   locking is causing contention or stale reads.
2. **More than one host or process writes the run registry.** The single-writer
   assumption behind the file/SQLite ledger no longer holds (e.g. a scheduler on one
   box, an API on another).
3. **Operational queries the file/SQLite layout serves poorly** — indexed point
   lookups, retention/expiry sweeps, or alerting joins over the status tables that
   want a real query planner and indexes.

If none of these is true, the correct action is to **do nothing** and leave the
metadata tier on SQLite. Record that you checked and declined, so this isn't
re-litigated.

## What the work is, when triggered

- Add a Postgres-backed implementation of the metadata/serving stores **behind the
  existing `StorageRepository` port** (and the per-store interfaces M1 keeps narrow).
  No caller imports Postgres directly; no caller changes. If a caller has to change,
  M1's port wasn't narrow enough — fix that first.
- Keep Parquet as the system of record. For the frontend, **project** the small
  serving/aggregate views into Postgres rather than forking the source of truth;
  DuckDB-attach-Postgres can build those projections without a second pipeline.
- Make the backend choice configuration, not code: the same orchestration runs on
  SQLite locally and Postgres in a deployed setting, selected at the port boundary.

## Test surface

Read [TESTING.md] first. Specific to M10:

- **Port conformance, unchanged.** The Postgres backend passes the exact same
  `StorageRepository` / per-store contract tests the SQLite backend passes — same
  tests, swapped backend. If a test needs to know which backend it's on, the port is
  leaking.
- **The data plane is untouched.** Assert no data-plane read/write path acquired a
  Postgres dependency; the M7 byte-identical replay and provenance tests still pass
  with zero changes. This task must be invisible to the data plane.
- **Backend equivalence.** A round-trip through the run/positions/risk/triage/universe
  stores returns the same logical results on SQLite and Postgres (independently
  derived expected values, per TESTING.md — not copied from either backend).
- **Concurrency, if trigger 1 or 2 drove this.** Demonstrate the specific
  multi-writer / live-read scenario that justified the move now works — the failing
  case is the acceptance test.

## Done criteria

The metadata/serving tier runs on Postgres in the deployed configuration and SQLite
locally, both behind one unchanged port; the data plane is provably untouched (M7
headline tests green, no Postgres import below the serving tier); the triggering
scenario is covered by a real test; gate green. If no trigger has fired, "done" is a
one-line note in this file recording that we checked and stayed on SQLite.

## Gotchas

- **Do not move the data plane.** If anyone proposes putting the numbers in Postgres,
  re-read the argument above — it breaks byte-identical replay and trades zero-ops
  files for a server. The answer is no.
- **Behind the port or not at all.** A Postgres dependency that any caller imports
  directly defeats the entire point and makes the next backend change a rewrite.
- **Don't pre-build it.** A Postgres server with no concurrent reader/writer is pure
  maintenance cost. Wait for a trigger; the discipline is *not* adopting it early.
- **One source of truth.** Postgres holds projected/serving copies of status, not a
  second authoritative record of the analytics. Parquet remains the system of record.
