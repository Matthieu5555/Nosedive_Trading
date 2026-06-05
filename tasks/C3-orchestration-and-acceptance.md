# C3 — Orchestration, observability, and the headline acceptance tests on the `packages/` stack

- **Owns:** `packages/infra/src/algotrading/infra/{orchestration,observability}/**` (+ READMEs) and the cross-cutting acceptance tests in `packages/infra/tests`.
- **Depends on:** C1 (the actor — the one driver), C2 (qc/validation), M1/M2/M3. **Converges last.**
- **Blocks:** C5 (retiring `backend/orchestration` and `backend/tests`).
- **State going in:** unstarted in `packages` — the `orchestration`/`observability` skeletons are empty. The three headline tests **exist and are high quality**, but they live in `backend/tests`, drive only the dead flat stack, and are **not even in the root gate** (root `pyproject.toml` `testpaths` excludes `backend/tests`). They currently prove the four invariants on the tree we are about to delete.

## Objective

Wire the merged pieces into an operable product driven by the **one** C1 actor, and **prove the four invariants — determinism, immutable raw, provenance, same-code live+replay — hold end to end on the `packages/` stack, inside the root gate.** This is the step that lets us *prove* the merge closed rather than assert it.

## What to do

1. **Port our orchestration/observability around the one actor** from `backend/src/orchestration/**`: jobs (universe refresh, live collection, incremental analytics, EOD reconciliation, replay, QC), correlation-id tracing (collector session → analytics jobs), the five metrics (event rates, stale ratios, forward failures, solver failures, scenario run times), the four alerts (collector death, missing partitions, elevated failure rates, QC fails), the dashboard, `run_end_of_day`, kill-and-restart idempotency, and the `reconstruction/` subpackage (date-range driver, missing-partition flagging, versioned restatement, replay-vs-live compare).
2. **Adopt Vincent's richer helpers as jobs around the actor — never as a second driver:** `orchestration/{provider_flow,risk_pipeline,archive,compare,persist,positions_io,universe_io}.py` and `observability/{alerts,health,runner}.py`. Reconcile `provider_flow` (the multi-broker driver) so it feeds **our actor**. Drop Vincent's `pipeline.py` as a driver.
3. **Relocate the three headline tests onto the `packages/` stack and into the gate** — the crux of the whole convergence:
   - `test_replay_byte_identical.py`, `test_provenance_verification.py`, `test_handover_e2e.py` move from `backend/tests` to `packages/infra/tests`, re-pointed to `algotrading.infra.*`, driving the **ported** actor (not a stub, not the old tree).
   - **Add `packages/infra/tests` to the root `pyproject.toml` `testpaths`** so the workspace gate actually runs the acceptance bar.
   - Confirm byte-identity, provenance, and **cross-process `config_hash` stability** hold on the packages stack.

## Frozen seam

Everything persists through M1's `StorageRepository`; analytics come from M2/M3 via the actor; QC/validation from C2. C3 adds no math — it schedules, traces, persists, alerts, and replays.

## Test surface (the headline guarantees — must be real, on the packages stack, in the gate)

- **Same-code-path replay (byte-identical):** drive the actor once from a simulated live stream and once from the same events replayed off stored raw partitions; assert snapshots/forwards/surfaces/risk are byte-identical (`ActorOutputs ==` and Parquet bytes `==`), multi-underlying.
- **Provenance verification:** walk every M2/M3 output landing in storage; assert a non-empty, well-formed stamp (hash recompute, `code_version`/`config_hash`/lineage, causal `source_ts ≤ calc_ts`); include a tampered-stamp rejection.
- **Orchestration robustness:** kill-a-job-mid-run leaves no duplicated/corrupted outputs and is recoverable; a missing partition is flagged, never silently interpolated; restated outputs write to versioned partitions and the old partition survives; a simulated failure is detected within the documented interval (injected clock); correlation IDs resolve a session → its analytics jobs; replay and live align on overlapping dates under one code version.
- **Handover e2e:** the scripted new-engineer run (bootstrap → connectivity smoke → reconstruct a day → run QC), each stage producing a real artifact.

## Done criteria

One orchestration/observability layer driving the **one** actor (no second driver); both headline tests green end to end **on the merged stack and inside the root gate**; robustness + handover green; the four invariants demonstrably hold across `packages/`. `backend/orchestration` + the migrated `backend/tests` are now stale — hand them to C5.

## Gotchas

One driver — the actor. If `provider_flow` or any helper re-introduces a second analytics path, the byte-identical test becomes a lie; route everything through the actor. The headline tests are acceptance, not smoke — they must exercise the real merged code. A headline test that isn't in the gate isn't a guarantee.
