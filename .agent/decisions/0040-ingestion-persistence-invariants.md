# 0040 — Ingestion persistence invariants: raw-before-derived, complete-or-flagged, one persist orchestrator

- **Status:** accepted, 2026-06-10 (owner ruled OQ-C/OQ-D 2026-06-10). Lands **WS
  T-raw-invariant** ([`../../tasks/T-raw-invariant.md`](../../tasks/T-raw-invariant.md)).
  **Coordinate with the in-flight QA-FIX** (TASKBOARD 2026-06-08) — see Consequences.
- **Date:** 2026-06-10.
- **Implements:** blueprint **Part I architecture** ("No downstream layer may silently
  overwrite an upstream observation"; determinism), **Part IV Step 3** ("the raw layer is the
  evidentiary record … persist every event to the raw layer"), **Part XIX final-reminders**
  ("Do not hide fallback behavior … that fact must be queryable") under
  [[0011-blueprint-as-plan-of-record]].
- **Relates to:** [[0019-one-immutable-raw-model]] (the append-only raw layer this protects),
  [[0027-collection-seam-push-canonical]] (the collection seam that lands raw),
  [[0026-orchestration-observability-reconciliation]] (the actor-driven orchestration these
  invariants bind), [[0015-storage-repository-port-tiered-backends]] (the `RunRepository`/
  ledger), [[0028-configuration-and-reproducibility-standard]] (per-run manifest — reproducibility,
  which these invariants complement with *completeness*), [[0016-eventsource-seam-backtest-readiness]]
  (replay sources read raw; they must find it present).

## Context

A code audit of the ingestion stack (2026-06-10) found that **raw-landing is a conditional
side-effect, not an invariant**, and that **five uncoordinated persist entrypoints each write a
different subset of tables**:

| Entrypoint | lands `raw_market_events` | persists derived | emits `projected_option_analytics` |
|---|:--:|:--:|:--:|
| `eod_run` / `eod_stages._collection`+`_analytics` | yes, if a non-empty basket reaches `_collection` (`eod_stages.py:334`) | yes (`driver.py:persist_outputs`) | yes (only path passing `provider=`) |
| `run_incremental_analytics` (`jobs.py`) | no (reads raw) | yes | no |
| `reconstruct_day` (`reconstruction/batch.py`) | no (requires raw present) | yes | no |
| `build_surface` (`surface_job.py`) | yes via `collect_live` | yes | no |
| `run_provider_flow` | yes (raw only) | no | — |

`persist_outputs` (`driver.py:985-1009`) writes nine derived tables but `if not records:
continue` — an empty output **silently skips its table**, so a `(trade_date, underlying)` can
land asymmetrically (surface without grid; snapshots without raw). This is exactly the observed
**SX5E 2026-06-10** state: every derived table persisted, `raw_market_events` absent,
`projected_option_analytics` absent — consistent with a persist by some path **other than
`eod_run`** that wrote derived without landing raw and without passing `provider=`.

The blueprint forbids all three: it mandates "persist every event to the raw layer" (Step 3),
"no downstream layer may silently overwrite an upstream observation" and determinism (Part I),
and "do not hide fallback … that fact must be queryable" (Part XIX). No current ADR records
these as invariants — the superseded actor-wiring decision (ADR 0020, removed — git history)
assumed raw off the raw layer, and [[0027-collection-seam-push-canonical]] fixes the *push seam*
but not the *persist ordering*. So this is **ungoverned drift**, and this ADR records the missing decision.

## Decision

Four invariants on the ingestion/persist boundary. They are enforced **once, structurally** —
the goal is to retire the divergent entrypoints, not to add a sixth guard on top of them.

**1. Raw-before-derived.** No derived table is persisted for a `(trade_date, underlying)`
unless the raw it derives from is present in `raw_market_events`. A capture path lands raw
*first* (append-only, content-addressed) and only then persists derived; a replay path
(`reconstruct_day`, `run_incremental_analytics`) **requires** raw present and is read-only of
it. Enforced at the persist boundary, not by convention.

**2. One persist orchestrator owns capture → land-raw → analytics → persist.** The five
entrypoints converge on one sequenced owner for the *capture-and-persist* case; the
*replay-of-stored-raw* case stays separate but is explicitly labeled read-only-of-raw.
`provider=` is threaded consistently so `projected_option_analytics` is produced wherever the
grid is expected — and where it is intentionally absent (provider-less replay), that absence is
**explicit and logged**, never a silent empty.

**3. Complete-or-flagged day.** `persist_outputs` stops silently skipping. An empty derived
output for a day that **has** raw is recorded as an explicit *empty/flagged* state with a
reason code (mirroring `reconstruction/batch.py`'s existing `MISSING` vs `EMPTY` distinction),
so "ran and found nothing" is queryable and distinct from "never ran". This is the Part XIX
"do not hide fallback" rule applied to persistence.

**4. Per-run completion marker.** The run ledger records a per-**run** completion, not only
per-**stage**, so a partial run is detectable and a restart re-runs *to completeness* instead
of skipping a stage whose siblings never finished. This is the lighter discipline the blueprint
actually asks for (dependency-ordering + idempotency + partial-data flags, Part IV Step 15 /
Part XIII Step 13) — **not** full ACID transactions, which the blueprint does not mandate.

## Rulings (owner, 2026-06-10)

- **OQ-C — raw missing at a derived persist → fail-hard capture, flag replay.** On the capture
  path a derived persist without its raw is a **bug**, not a state → raise. On the replay path a
  missing raw partition is a known reconstruction outcome → flag `MISSING` (the existing
  `reconstruction/batch.py` distinction).
- **OQ-D — boundary with QA-FIX → fold #3/#4 into QA-FIX, keep #1/#2 separate.** QA-FIX already
  owns `storage/adapter.py` (silent-empty **read**) and `run_state.py` (ledger **lock**); the
  silent-empty **write** (#3) and per-run **completion** (#4) deltas go into those same files
  under the QA-FIX owner to avoid a shared-tree collision. The entrypoint convergence + raw-
  present guard (#1/#2) stay as the separate **T-raw-invariant**, sequenced after QA-FIX lands.

## Consequences

- The **SX5E class of bug** (derived persisted without raw) becomes structurally impossible:
  no derived without raw, no silent partial day.
- **Coordination is mandatory.** T-raw-invariant overlaps QA-FIX on `eod_runner.py`,
  `run_state.py`, `collectors/*`, `cp_rest_close_capture.py`, `storage/adapter.py` (shared tree —
  see `tasks/TASKBOARD.md`). The task is **blocked-by/sequenced-after QA-FIX** and must re-confirm
  scope at claim time.
- Replay/reconstruction semantics are unchanged in behaviour but made **explicit** (labeled
  read-only-of-raw; provider-less grid absence logged).
- Pairs with [[0039-raw-schema-bridge-and-sample-regeneration]]: 0039 makes a captured raw day
  *exportable* as a sample; 0040 makes the raw *always there to export*. Together they restore
  the blueprint's "regression library from captured raw" loop end-to-end.
