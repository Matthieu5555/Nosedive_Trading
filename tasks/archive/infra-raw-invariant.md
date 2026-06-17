# T-raw-invariant — Enforce raw-before-derived + converge the persist entrypoints

> **READY (un-blocked 2026-06-17) — high care.** QA-FIX (`fix/live-spine-wiring`) **landed on
> main** as `aca7369` ("raw is faithful — capture every observed row; two-sided gate moves to the
> derived layer"); the sequencing blocker is cleared. That commit resolved the *quarantined-rows*
> hole (ingestion-audit F1 = every observed row now reaches raw, gate is derived-only), but it did
> **not** touch `persist_outputs`: the silent-empty *write* (`driver.py:821` `if not records: continue`)
> is **still live** and now folds into this task's scope (the §4 delta QA-FIX was supposed to own
> but did not).
> [ADR 0040](../.agent/decisions/0040-ingestion-persistence-invariants.md) accepted (OQ-C
> fail-hard-capture/flag-replay). **This task now owns: the raw-present guard + entrypoint
> convergence (#1/#2) AND the silent-empty-table write (#4 — zero-row sentinel/manifest instead of
> `continue`).** Still touches `run_state.py`, `collectors/*`, `cp_rest_close_capture.py`,
> `storage/adapter.py` — shared tree, claim explicit paths, do not collide.

- **Owns (subject to QA-FIX boundary, OQ-D):** the persist boundary
  `packages/infra/src/algotrading/infra/actor/driver.py` (`persist_outputs`); the persist
  entrypoints `orchestration/{eod_stages,jobs,surface_job}.py` + `reconstruction/batch.py`
  (convergence + raw-present guard); `orchestration/run_state.py` (per-run completion marker —
  **coordinate with QA-FIX's ledger lock**); the regression tests under
  `packages/infra/tests/`.
- **Depends on:** ADR 0040 accepted; QA-FIX landed (sequencing). A real SX5E raw partition needs
  a gateway re-capture (outward-facing; owner go) — but the invariant + tests do **not** need the
  broker.
- **Blocks:** nothing downstream, but it closes the **SX5E-class bug** (derived persisted without
  raw) and makes a captured day exportable end-to-end (pairs with T-bridge).
- **State going in:** raw-landing is conditional (`eod_stages.py:334`, only if a non-empty basket
  reaches `_collection`); five entrypoints persist different subsets (only `eod_run` lands raw
  **and** passes `provider=`); `persist_outputs` (`driver.py:1007`) `if not records: continue`
  silently skips empty tables; the run ledger (`run_state.py`) is per-stage, not per-run. Observed:
  SX5E 2026-06-10 has every derived table but no `raw_market_events` and no
  `projected_option_analytics`.

## Objective

Make "no derived without raw" and "complete-or-flagged day" structural invariants, enforced once,
so no entrypoint can persist a partial/raw-less day — without stacking a sixth ad-hoc guard.

## What to do (ordered)

1. **Raw-present guard at the persist boundary (invariant #1).** Before `persist_outputs` writes
   any derived table for a `(trade_date, underlying)`, assert the raw partition is present. Per
   OQ-C: **fail-hard** on the capture path, **flag** (`MISSING`, reuse `reconstruction/batch.py`'s
   distinction) on the replay path. Red test first: persist derived with no raw → raises (capture)
   / flags (replay).
2. **Complete-or-flagged day (invariant #3) — → QA-FIX per OQ-D; listed here for the full invariant
   set, executed under the QA-FIX owner.** `persist_outputs` stops silently skipping: an empty
   derived output for a day that *has* raw lands an explicit empty/flagged marker with a reason
   code, distinct from "never ran". **Coordinate with QA-FIX's `storage/adapter.py` silent-empty
   *read*** — this is the *write* side; align the empty/missing vocabulary.
3. **Converge the entrypoints (invariant #2).** One sequenced owner for capture→land-raw→analytics
   →persist; `run_incremental_analytics`/`reconstruct_day` explicitly labeled read-only-of-raw.
   Thread `provider=` consistently; where the grid is intentionally absent (provider-less replay),
   **log** the absence — never a silent empty. Test: each entrypoint either lands raw or asserts it
   present.
4. **Per-run completion marker (invariant #4) — → QA-FIX per OQ-D (lives in `run_state.py` alongside
   QA-FIX's ledger lock).** Extend the ledger so a run records completion (not
   only per-stage); a restart re-runs to completeness rather than skipping a stage whose siblings
   never finished. **Coordinate with QA-FIX's ledger lock** (concurrency) — this is *completion*,
   orthogonal. Test: a simulated mid-run crash leaves the run flagged incomplete; restart converges.
5. **Regression test — the SX5E shape.** A test that asserts: for any persisted derived
   `(trade_date, underlying)`, `raw_market_events` for that key exists. This is the oracle that
   would have caught the SX5E loss.

## Done when

Root gate green; the SX5E-shape regression test passes; raw-present guard covered both directions
(fail-hard capture, flag replay); empty-vs-missing write side aligned with QA-FIX's read side; the
five entrypoints covered by tests asserting raw-present-or-landed. Run the `check-lookahead-bias`
skill on any touched as-of/persist path. ADR 0040 marked landed.
