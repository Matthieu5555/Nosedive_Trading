# M7 — Orchestration, observability, replay, and the headline acceptance tests

- **Branch:** `feat/merge-orchestration`
- **Owns:** `packages/infra/src/algotrading/infra/{orchestration,observability}/**` (+ READMEs), and the cross-cutting acceptance tests.
- **Depends on:** M0, M1, M2, M3, M4, M6. **Converges last** — it wires the pieces and verifies everyone's invariants.
- **Blocks:** nothing; this closes the loop.

## Objective

Wire the merged pieces into an operable product driven by the M4 actor, and prove the four invariants (determinism, immutable raw, provenance, same-code live+replay) hold end to end. Merge both repos' orchestration; the headline tests are the system's guarantees and must be real, not prose.

## What to merge

- **Keep ours:** the orchestration/observability we built around the actor — jobs (universe refresh, live collection, incremental analytics, EOD reconciliation, replay, QC), correlation-id tracing linking collector sessions to analytics jobs, the five metrics (event rates, stale ratios, forward failures, solver failures, scenario run times), the four alerts (collector death, missing partitions, elevated failure rates, QC fails), the dashboard, `run_end_of_day`, kill-and-restart idempotency, and the reconstruction subpackage (date-range driver, missing-partition flagging, versioned restatement, replay-vs-live compare). See `backend/src/orchestration/**` (incl. `reconstruction/`).
- **Adopt from Vincent:** his orchestration breadth where richer — `infra/orchestration/{provider_flow,risk_pipeline,archive,compare,persist,positions_io,universe_io}.py` and `infra/observability/{alerts,health,runner}.py`. His `provider_flow` is the multi-broker driver; reconcile it so it feeds **our actor**, not his removed pipeline.
- The driver is the M4 actor. Vincent's `pipeline.py` as a *driver* is dropped (M4); its useful orchestration helpers (archive/compare/persist) are kept as jobs around the actor.

## Frozen seam

Everything persists through M1's `StorageRepository`; analytics come from M2/M3 via the actor; QC/validation from M6. M7 adds no math — it schedules, traces, persists, alerts, and replays.

## Test surface (the headline guarantees — must be real)

Read [TESTING.md] first. M7 owns the system's two headline tests:
- **Same-code-path replay (byte-identical).** Drive the actor once from a simulated live stream and once from the same events replayed off stored raw partitions; assert snapshots/forwards/surfaces/risk are byte-identical (ActorOutputs `==` and Parquet bytes `==`). Carry ours: `test_replay_byte_identical.py`.
- **Provenance verification.** Walk every M2/M3 output landing in storage and assert a non-empty, well-formed provenance stamp — the determinism/provenance the other streams claim, checked not trusted. Carry ours: `test_provenance_verification.py`.

Plus orchestration robustness: kill-a-job-mid-run leaves no duplicated/corrupted outputs and the last-healthy-run/backlog is recoverable; a missing partition is flagged, never silently interpolated; restated outputs write to versioned partitions and the old partition survives; a simulated failure is detected within the documented interval (injected clock); correlation IDs resolve a collector session → its analytics jobs end to end; replay and live align on overlapping dates under the same code version. Carry ours `test_handover_e2e.py` (the scripted new-engineer run).

## Done criteria

One orchestration/observability layer driving the M4 actor (no second driver), both headline tests green end to end on the merged stack, robustness tests green, gate green. The four invariants demonstrably hold across the merged repo.

## Gotchas

One driver — the actor. If `provider_flow` or any helper re-introduces a second analytics path, the byte-identical test becomes a lie; route everything through the actor. Prefer fewer well-labeled metrics over many opaque ones. The headline tests are acceptance, not smoke — they must exercise the real merged code, not a stub.
