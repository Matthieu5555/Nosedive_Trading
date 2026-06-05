# 0007 — Integration & operations: actor seam, storage versioning, dual-path ban

- **Status:** accepted
- **Date:** 2026-06-02

## Context

Workstream E (`src/actor`, `src/qc`, `src/orchestration`, `documentation/`) is steps 13–16:
the Nautilus actor, the QC/validation framework, orchestration and observability,
historical reconstruction/replay, and the operational handover. It converges last
and its two headline tests — same-code-path byte-identical replay, and provenance
verification over every C/D output landing in storage — are the guarantees the
whole architecture exists to make true. A new owner resumed E after the prior
session stopped just past the seam freeze (gate green, actor interfaces frozen as
`NotImplementedError` stubs, no bodies, no QC/orchestration/docs). The choices
below are not obvious from the code and would otherwise be re-litigated.

## Decision

1. **The "Nautilus actor" is a framework-free pure driver, not a `nautilus_trader`
   subclass.** `nautilus_trader` is not a dependency and is not installed; B already
   built a broker-agnostic connectivity seam and a `collectors.replay_day` over the
   immutable raw layer. The actor is therefore a plain function pipeline
   (`run_analytics` is a pure function of its inputs; `run_day` reads the day's raw
   events via `replay_day` and feeds them in). This is exactly the "feed those same
   functions from a plain loop if Nautilus ever gets in the way" escape hatch in
   `BIG_PICTURE.md`, and it is what makes same-code-path replay achievable here:
   live and replay differ only in who populated the raw layer first, not in the
   compute path. The Nautilus name is kept for continuity with the roadmap.

2. **Compute is separated from persistence so replay can compare values, not
   files.** `run_analytics(...) -> ActorOutputs` holds every derived contract for
   one as-of instant and touches no I/O and no clock; `persist_outputs(store, ...)`
   writes them. The headline replay test drives `run_analytics` from a live event
   stream and from the same events replayed off disk and asserts the two
   `ActorOutputs` are equal — a plain `==` over frozen dataclasses — which is a
   stronger, cheaper check than diffing Parquet bytes (and the bytes follow from the
   values once persisted). `calc_ts`/`as_of` are injected, never read from a clock,
   which is what makes the stamps and therefore the outputs reproducible.

3. **Versioned partitions are an additive, default-off sub-partition of A's
   storage.** Step 13 requires that a restated/replayed analytic written under newer
   code preserve the older one rather than overwrite it. A's derived layers replace
   in place, keyed only on `(trade_date, underlying)`. We add an optional fourth
   path segment, `version=<V>`, threaded through `write`/`read`/`delete_partition`
   plus a new `list_versions`. `version=None` (the live recompute path) reproduces
   the original three-segment layout **byte-for-byte**, so A's existing storage suite
   is untouched and every pre-versioning partition is unchanged; only the restatement
   path passes an explicit version. This is no contract, schema, or primary-key
   change — the version is a physical placement, not a contract column.

   This edits A's `src/storage`, which `storage/README.md` rule 4 says is A-owned and
   routed through A, not edited in place by a consumer. The crossing is deliberate
   and was approved for this workstream; it is recorded here and claimed on the
   TASKBOARD rather than made silently. The alternative — E routing restated outputs
   to a separate table namespace or root, leaving storage untouched — was rejected as
   the less clean of the two: it would split one analytic family across two physical
   homes and duplicate the partition-management surface.

4. **One code path for live and replay; no "historical-only" fork.** All derived
   analytics come from the immutable raw layer through `run_day`. There is no second
   reconstruction path, because dual paths drift and the drift is exactly what the
   replay test is built to catch. Historical reconstruction is `run_day` over a date
   range, writing restatements to versioned partitions (decision 3), not a separate
   engine.

5. **E is behavior-tested, not coverage-gated.** Per ADR 0004 §3 and TESTING.md, the
   pure-function core (C and D) carries the branch-coverage floor; E's transport and
   orchestration tiers are held to named behavior tests (kill-and-restart
   idempotency, missing-partition flagging, detection-within-interval on an injected
   clock, correlation-id trace resolution, the two headline invariants). E therefore
   does **not** touch `[tool.coverage] source` in `pyproject.toml`. The three op
   dependencies (`structlog`, `prometheus-client`, `apscheduler`) are the only
   `[project]` additions.

## Addendum (2026-06-02) — version-blind read semantics

Decision 3 fixed the on-disk *layout* (`version=None` keeps the original
three-segment path) but left the *read* semantics under-specified, and the first
implementation got them wrong: a version-blind `read` globbed the whole partition
and returned the live rows **and** every restatement together. That double-counts —
the live partition and a `version=<V>` restatement coexist for the same
`(trade_date, underlying)` (decision 4's reconstruction writes the restatement
*beside* the live partition), and they share primary keys. It also quietly corrupted
`compare_replay_to_live`, which reads the "live" rows back with `version=None`.

Resolved: `read(..., version=None)` returns the **live (unversioned) rows only**; an
explicit `version=<V>` returns only that restatement; the two are read back
separately and never mix. A partition holding only restatements has no live rows, so
a version-blind read of it is empty (inspect via `list_versions`). Separately,
versioned writes are now restricted to derived tables — a versioned write to an
append-only layer is refused (`VersionedWriteNotAllowed`), since a raw observation is
immutable and has no restatement. The "`version=None` reproduces the original layout
byte-for-byte" claim above stands for *path layout*; it was never a promise about the
*result set* of a version-blind read, which is what this addendum pins.
