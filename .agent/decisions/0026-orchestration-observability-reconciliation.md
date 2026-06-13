# 0026 — Orchestration/observability: one actor-driven layer; which helpers we adopted

> **AMENDED 2026-06-13 (T-index-only-refactor).** "`provider_flow` (the multi-broker capture
> driver)" is now single-broker (IBKR only; Saxo/Deribit removed). The orchestration decision is
> otherwise unaffected. See ADR 0023's amendment.

- **Status:** accepted, 2026-06-05. Records the C3 design choice the spec
  (`tasks/C3-orchestration-and-acceptance.md`, step 2 — "adopt Vincent's richer helpers as
  jobs around the actor") left to the implementer.
- **Date:** 2026-06-05
- **Relates to:** [[0007-integration-ops]] (no-dual-path invariant — the reason this matters),
  [[0023-nautilus-runtime-spine-and-library-leverage]] (library-leverage rule + Nautilus spine),
  [[0015-storage-repository-port-tiered-backends]] (our `RunRegistry`/`ParquetStore` is the
  storage spine), [[0010-qc-validation-merge]] (the QC/triage seam the QC job rides).

## Context

C3 ports our orchestration/observability around the **one** actor and was also told to
"adopt Vincent's richer helpers as jobs — never as a second driver"
(`orchestration/{provider_flow,risk_pipeline,archive,compare,persist,positions_io,universe_io}`,
`observability/{alerts,health,runner}`). On inspection those helpers ride **Vincent's storage
and risk model** — `DerivedStore`, `UniverseStore`, `OptionContractRow`/`UnderlyingRow`/
`PositionRow`, and a `risk_pipeline` that re-assembles a `RiskSnapshot` from a surface. Our merge
locked **our** actor as the analytics spine and **our** M1 `ParquetStore` + contracts + M10
`RunRegistry` as the storage spine. None of `DerivedStore`/`UniverseStore`/`*Row` exist on our
stack. Adopting those helpers verbatim would re-introduce a parallel storage model and, in the
case of `risk_pipeline`, a **second** surface→risk path — exactly what makes the byte-identical
guarantee a lie (ADR 0007; C3 spec gotcha).

## Decision

**1. One orchestration layer, ours, driving the one actor.** `orchestration/{jobs,qc_job,metrics,
alerts,dashboard,run_state,storage_root,pipeline}` + the `reconstruction/` subpackage are ported
onto `algotrading.infra.*`. The actor is the only analytics driver; these are jobs around it.

**2. Adopt from Vincent only what rides our spine and adds something ours lacks.**
- **`observability/runner.run_job`** — *adopted*. It records run lineage (a `core.manifest.Manifest`
  in our landed M10 `RunRegistry`: run id, env, code version, config hashes, in/out partitions,
  correlation id, status; failed-then-reraise; run-id idempotency). It rides our storage and is
  genuinely additive over the EOD stage ledger (`run_state`), so it lands as the `observability/`
  package.

**3. Decline the helpers that would fork the spine or duplicate ours** (with the equivalent we
already have):
- `archive`, `persist` → our `actor.persist_outputs` + `reconstruction._persist_outputs`
  (versioned). They target Vincent's `DerivedStore`.
- `compare` → our `reconstruction.compare_replay_to_live` (per-table, by primary key then value).
- `risk_pipeline` → the **actor** already does surface→risk via `valuation_join`; a second
  assembler is a second analytics path (forbidden).
- `positions_io`, `universe_io` → map Vincent's `PositionRow`/`UniverseStore`, which we did not
  adopt; our storage handles `Position`/`InstrumentMaster` rows directly.
- `observability/health` → our `orchestration.dashboard.build_dashboard` + `run_state` answer the
  same operator questions on our stack (it targets Vincent's `DerivedStore`).

**4. Defer the collection-coupled and serving-coupled pieces** (not declined — blocked):
- `provider_flow` (the multi-broker capture driver) and the live-collection job depend on C1's
  broker-session→`RawMarketEvent` seam, which is unreconciled on the packages stack (the pull
  `SessionSupervisor` yields a `contracts.BrokerTick`; the push `RawCollector` ingests the EAV
  `collectors.BrokerTick`). Owner-deferred ("priorité IBKR, le reste en stand by"). **Resolved by
  [[0027-collection-seam-push-canonical]]** (the push `RawCollector` is canonical; the pull seam is
  harvested for `sequence` idempotency + `SessionSupervisor`, then retired); the follow-on port is
  `tasks/C6-collection-seam-unification.md`. The EOD
  collection stage stays an **injected seam** on `run_end_of_day`; it lands its live wiring when
  C1 closes the seam. `observability/alerts` (escalation→channel routing) and live health
  endpoints are serving/API-tier and handled there.

## Consequences

C3's actor-driven engine is complete and gate-green on `packages/`: the byte-identical replay,
provenance, reconstruction-robustness, orchestration-behavior, observability-lineage, and the
handover engine path (bootstrap→reconstruct→QC) all run inside the root gate. The spec's "adopt
Vincent's helpers" line is satisfied by *one* adoption (`run_job`) plus a documented map of
ours-already-covers-it; nothing forks the storage spine or the analytics driver. Two clearly
documented skips mark the collection-blocked cases (the two live-collection orchestration tests
and the handover connectivity-smoke stage). When C1 lands the collection seam, the deferred items
(`collect_live`, the live collection stage, `surface_job`, `provider_flow`, the smoke stage) are
the follow-on; none requires reopening this layer.
