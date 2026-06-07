# Archived taskboard claims

Finished claims moved off `tasks/TASKBOARD.md`, newest first, each with a one-line
note on what was done so "why was this changed" stays answerable later (the rule in
`tasks/TASKBOARD.md`). A claim lands here when its workstream is complete and its
gate is green; the commit/merge of the branch is a separate step.

## Completed workstream specs

The original five-workstream backbone build (A–E) is complete. Its specs live here
as the record of what was built and why, superseded by the merge workstreams
(`M0`–`M9`) on the active board:

- [01-foundation-data-platform.md](01-foundation-data-platform.md) — A: contracts, config/provenance, storage, fixtures, gate.
- [02-market-data-plane.md](02-market-data-plane.md) — B: connectivity, universe, collectors.
- [03-analytics-core.md](03-analytics-core.md) — C: snapshots, forwards, IV, surfaces, pricing.
- [04-risk-engine.md](04-risk-engine.md) — D: greeks, aggregation, scenarios, reconciliation.
- [05-integration-operations.md](05-integration-operations.md) — E: actor, QC, orchestration, replay, handover.

The merge does not throw this away — each merge workstream bakes the A–E
implementation off against Vincent's and keeps the better.

The ten-workstream merge fan-out (M0–M10) that followed A–E is now archived too. Its
keystone + bottom half (M0 core/contracts seam, M1 storage, M2 analytics, M3 risk, and
the M10 Postgres serving tier) **landed** in `packages/infra`; its top half (market-data,
actor, qc, orchestration, frontend) was either stuck in the old flat tree or forked, and
was finished by the **convergence tasks (C1–C8)**, which are now complete and archived
below. The fan-out decisions live in `.agent/decisions/` and the results in the code.

- [M0-monorepo-keystone.md](M0-monorepo-keystone.md) — layered uv-workspace, merged `core`, frozen `contracts` seam + gate. Landed.
- [M1-storage.md](M1-storage.md) — `ParquetStore` behind `StorageRepository`, immutable raw EAV. Landed.
- [M2-analytics-core.md](M2-analytics-core.md) — snapshots/forwards/iv/surfaces/pricing, frozen pricer. Landed.
- [M3-risk-engine.md](M3-risk-engine.md) — greeks/aggregation/scenarios/reconciliation on the M2 seam. Landed.
- [M4-market-data-actor.md](M4-market-data-actor.md) — market-data plane + actor spine. Built in flat tree; relocated in **C1** (done).
- [M5-broker-adapters.md](M5-broker-adapters.md) — IBKR/Saxo/Deribit leaves. Forked against ADR 0020; the leaf packages landed in **C1**, the collection-seam fork resolved in **C6** (ADR 0027) (done).
- [M6-qc-validation.md](M6-qc-validation.md) — QC + validation/triage. Contract collapsed; logic ported in **C2** (done).
- [M7-orchestration-observability.md](M7-orchestration-observability.md) — orchestration/observability + acceptance tests. Ported + gated in **C3** (done).
- [M8-frontend.md](M8-frontend.md) — FastAPI BFF + React/Vite web. Two copies; consolidated in **C4** (done).
- [M9-discipline-docs.md](M9-discipline-docs.md) — blueprint, vol-surface docs, notebooks, ADRs, glossary. Landed; doc upkeep is now continuous per `AGENTS.md`.
- [M10-postgres-serving-tier.md](M10-postgres-serving-tier.md) — `RunRepository` + SQLite/Postgres behind M1's port. Landed; further expansion is trigger-gated.

The **convergence series (C1–C8)** that closed the merge is complete and merged to `main`;
its specs are archived here as the record of what was built. The linear runbook that
sequenced them is [CONVERGENCE-PLAN.md](CONVERGENCE-PLAN.md). The last convergence task, **C7**
(config hardening), **landed in full** (tasks 1–5 + carry-forwards, 2026-06-07) and its spec now
lives here: [`C7-config-hardening.md`](C7-config-hardening.md).

- [C1-actor-and-market-data-plane.md](C1-actor-and-market-data-plane.md) — market-data plane + actor spine relocated to `packages`; Nautilus runtime spine (ADR 0023/0025); IBKR on Nautilus + the custom Client-Portal REST transport (ADR 0024). Done.
- [C2-qc-validation.md](C2-qc-validation.md) — QC + validation/triage ported to `packages/infra`, one `triage_records` table (ADR 0010). Done.
- [C3-orchestration-and-acceptance.md](C3-orchestration-and-acceptance.md) — orchestration/observability + the four headline acceptance tests, in the root gate (ADR 0026). Done.
- [C4-frontend.md](C4-frontend.md) — the two frontends consolidated into `apps/frontend` on real infra seams. Done.
- [C5-retire-backend.md](C5-retire-backend.md) — the flat `backend/` tree deleted; docs/symlinks repointed onto `packages/infra`. Done. (The `Vincent's Code/` clone removal was permission-blocked and is carried forward on the active board — not a code item.)
- [C6-collection-seam-unification.md](C6-collection-seam-unification.md) — the collection seam unified on the push `RawCollector`; the four deferred use-cases ported (ADR 0027). Done.
- [C8-hygiene-fixes.md](C8-hygiene-fixes.md) — valuation-join + quote-quality seam tests; Deribit discovery determinism (inject `as_of`, not the wall clock); Saxo cleanups. Done.
- [ibkr-rest-api-evaluation.md](ibkr-rest-api-evaluation.md) — the research record behind the IBKR-over-REST decision (ADR 0024, accepted + landed). Reference, not an open spike.

The next epoch — the **index options-analytics pipeline** — is planned in
[`documentation/roadmap-index-analytics.md`](../../documentation/roadmap-index-analytics.md);
its per-workstream specs land on the active board as each phase opens.

| Who | Area / files | Branch | Done | What was done |
|-----|--------------|--------|------|---------------|
| Claude (agent) | `.agent/decisions/0011-0017`, `.agent/{glossary,map}.md`, `documentation/blueprint/`, `documentation/vol-surface/`, `notebooks/`, `tasks/TASKBOARD.md`, `README.md` | feat/merge-discipline | 2026-06-05 | M9 discipline layer complete: blueprint (20 files) + vol-surface pedagogy (doc + 18 figures + PDF) + 7 demo notebooks folded into `documentation/` and `notebooks/`; ADRs 0011–0017 (blueprint governance, per-broker packages, Deribit/Saxo adapters, storage repo port, EventSource seam, provider dimension) translated to English and merged into our ADR stream; glossary extended with crypto/Deribit/Saxo/broker-protocol vocabulary; map.md updated; root README updated to reflect monorepo merge state. All map links + documentation/modules symlinks verified green. Vincent's .claude/skills were all empty — no skills to merge. |
| Claude (agent) | `packages/infra/src/.../storage/{ports,runs,sqlite_runs,postgres_runs,factory}.py`, `packages/infra/tests/test_run_repository.py`, `packages/infra/pyproject.toml` | feat/merge-postgres | 2026-06-05 | M10 metadata/serving tier complete: `RunRepository` Protocol + `RunRecord`/`RunStatus`/`RunRegistry` (JSON-file reference) + `SqliteRunRepository` (local backend) + `PostgresRunRepository` (multi-host, `psycopg[binary]`, JSONB payload, ON CONFLICT idempotency) + `make_run_repository()` factory (POSTGRES_URL env var selects Postgres over SQLite). Optional dep `psycopg[binary]>=3.2` declared; lazy import so module loads without it. 23 conformance tests: structural port check, round-trip, ordering, idempotent overwrite, last_healthy semantics, job isolation, factory selection. 12 passed (SQLite), 11 skipped (Postgres: need POSTGRES_URL). Analytics data plane (Parquet/DuckDB) untouched. |
| codex | backend/src/{actor,qc,orchestration,storage}, backend/tests/test_{actor,qc,orchestration,provenance,replay,handover,storage}*.py, docs/, .agent/map.md, .agent/decisions/0007-integration-ops.md | feat/integration-ops | 2026-06-02 | Audited Workstream E implementation depth and claims. Reviewed actor/QC/orchestration/reconstruction/storage-versioning/docs/test coverage, ran the focused E test slice, the documented backend gate, and the coverage gate. Result: no blocking findings; gate green. |
| agent-E (claude) | backend/src/{actor,qc,orchestration}/** (new), docs/** (new), backend/tests/test_{actor,valuation_join,qc_checks,qc_report,orchestration,replay_reconstruction,replay_byte_identical,provenance_verification,handover_e2e}.py (new); +.agent/map.md rows (E backend + docs), ADR 0007; +[project] deps structlog/prometheus-client/apscheduler | feat/integration-ops | 2026-06-02 | Workstream E integration & operations (steps 13–16 + EOD run sequence + the five runbooks). Resumed after a prior session stopped post-seam-freeze. Built the framework-free actor (run_analytics/run_day; no nautilus_trader dep — ADR 0007 d1), the QC library of ten named checks with specific failing-object payloads + anomaly detection, orchestration/observability (jobs, correlation-id tracing, 5 metrics, 4 alerts, dashboard, run_end_of_day, kill-and-restart idempotency), and historical replay/reconstruction (date-range driver, missing-partition flagging, versioned restatement, replay-vs-live compare). Two headline tests PROVEN: same-code-path replay is byte-identical (ActorOutputs == and Parquet bytes ==) and every C/D output in storage carries a well-formed non-empty provenance stamp. Handover docs + scripted new-engineer e2e. Gate green: ruff/mypy clean (124 files), 574 pytest, pure-core branch coverage 98.53% (E behavior-tested, not coverage-gated — ADR 0007 d5). Pending commit on its branch. |
| agent-E (claude) — CROSS-WS | backend/src/storage/{partitioning.py,adapter.py,README.md}, backend/tests/test_storage.py (added cases); +ADR 0007 d3 | feat/integration-ops | 2026-06-02 | User-approved deliberate A-storage edit: an optional, default-off `version=<V>` sub-partition so a restated/replayed analytic preserves the older one instead of overwriting it (step 13). `version=None` reproduces the original `trade_date=/underlying=/data.parquet` layout byte-for-byte, so A's existing storage suite is untouched; no contract/schema/primary-key change. Added `list_versions` + optional `version` on write/read/delete_partition. Gate green; 8 new storage tests pin coexistence and the default-off layout. |
| agent-C (claude) | backend/src/{pricing,snapshots,forwards,iv,surfaces}, backend/tests/test_{pricing,pricing_properties,snapshots,forwards,iv,surfaces,seam_analytics,determinism_analytics}.py + tests/golden/, backend/pyproject.toml `[tool.coverage]` + QuantLib/py_vollib/scipy deps; 5 per-dir READMEs, .agent/map.md row, ADR 0004 | feat/analytics-core | 2026-06-01 | Workstream C analytics core (steps 5–10): frozen pricing keystone (pinned for D), snapshots → forwards → IV → surfaces, all pure (no I/O/clock/RNG) and stamped via A's `stamp`. Quote QC wired into the build path — `build_snapshots` assesses every snapshot and the batch keeps both the full and the QC-filtered `usable` view (step 7; review finding closed, ADR 0004 §5). Gate green: ruff/mypy/pytest, 364 tests, branch coverage 99.18% (90 floor). C→A seam + determinism (golden + cross-process hash) proven. Pending commit on its branch. |
