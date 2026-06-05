# TASKBOARD

The in-repo collision guard for a shared workspace with several humans and agents
working at once. **Before you start changing files, claim them here.** Clear your
claim when you're done. This is advisory, not enforced — it only works if every
actor reads and writes it, which is why `AGENTS.md` tells you to. What we actually
care about is the **working tree on the server being clean**: everything canonical
under `packages/` and `apps/`, the old `backend/` tree gone. Branches are optional
convenience, not the goal.

When a task is finished, clear its row. The record of *what* was built and *why*
lives in the code, the per-directory READMEs, and the ADRs in `.agent/decisions/`;
finished task specs move to `tasks/archive/`.

## Current phase: convergence — closing the merge

> **▶ START HERE: [CONVERGENCE-PLAN.md](CONVERGENCE-PLAN.md) is the linear A-to-Z runbook.**
> Work it top to bottom. It sequences the convergence tasks (C4/C5/C6) with the
> housekeeping the per-task specs don't own (commit C3, consolidate branches, retire the
> backend dupes in waves, tree hygiene) and the solidity bar the owner asked for
> (minimalism sweep, contract-test hardening, config-as-YAML). The `C*` specs below hold
> the per-task detail; the plan holds the order. Ground truth 2026-06-05: gate ~744 pass /
> 1 fail / ~18 skip — the one failure + all mypy/ruff are isolated in the uncommitted C3
> tree and the in-flight C4 frontend.

> **⚠ DIRECTION CHANGE — 2026-06-05 (read before touching C1, the actor, or any broker code).**
> The workspace owner reversed the framework-free / no-Nautilus stance. **Nautilus is now the
> runtime spine** (its data catalog + replay/backtest engine + actor host), the platform leans on
> every proven library it can, and **IBKR moves onto Nautilus's adapter while Vincent's
> Saxo/Deribit adapters are kept** (survivors, not deleted). This overturns the old C1 plan
> ("delete the M5 fork, retarget all three leaves onto the thin scalar `BrokerSession`") and the
> old "locked: framework-free actor." Authority:
> **[ADR 0023](../.agent/decisions/0023-nautilus-runtime-spine-and-library-leverage.md)**.
> If you are mid-C1 on the old plan, stop and re-read.

We merged two independent builds of the same system — this repo and Vincent's
(`github.com/Vincent-20-100/AlgoTrading` @ `refactor/audit-remediation`) — toward
the max-union of both. Two decisions are **locked**: adopt Vincent's **layered
uv-workspace monorepo** as the chassis, and — per
[ADR 0023](../.agent/decisions/0023-nautilus-runtime-spine-and-library-leverage.md) — make
**Nautilus the runtime spine**, leaning on proven libraries wherever one exists. Vincent's repo
is checked out read-only at **`Vincent's Code/`** (gitignored, not canonical) as a source of
inspiration; refresh with `git -C "Vincent's Code" pull`.

The earlier work happened in two waves, both archived in `tasks/archive/`:
- the original five-workstream backbone (A–E) — **complete**;
- the ten-workstream merge fan-out (M0–M10) — landed or superseded; its decisions live
  in `.agent/decisions/`, its results in the code, and the half-done parts are finished
  by the convergence tasks below.

**Where the tree actually stands** (audited 2026-06-05):
- **Converged, gate-green:** the bottom half — `core` + the frozen `contracts` seam
  (`StorageRepository`/`BrokerSession`), `storage`, the analytics core
  (`snapshots`/`forwards`/`iv`/`surfaces`/`pricing`/`utils`), and `risk`. All canonical
  in `packages/infra`; their `backend/src` copies are stale dupes.
- **Stuck in the old flat tree:** the market-data plane + the **actor spine** live only
  in `backend/src` (never relocated).
- **Broker plane (direction set by [ADR 0023](../.agent/decisions/0023-nautilus-runtime-spine-and-library-leverage.md)):**
  Vincent's vendored Saxo/Deribit slice in `packages/infra` (the EAV `BrokerTick` +
  `MarketDataAdapter`) is the **survivor** — kept, not deleted. IBKR moves onto **Nautilus's**
  shipped adapter. The scalar `contracts.BrokerSession` seam and the running-counter event id are
  reconciled in C1 (restore content-addressed ids; retire the unused pull seam). The 3 red
  Saxo-config tests are fixed there — the only red on the root gate.
- **Converged (C2, uncommitted):** `qc`/`validation` — the ten named checks + the
  anomaly/triage plane are now in `packages/infra` on the M0 seam, both feeding the one
  `triage_records` table (three sources), `TriageRow` dropped. `backend/{qc,validation}`
  are now stale dupes → C5.
- **Unstarted in `packages`:** `orchestration`/`observability` and the three headline
  acceptance tests (which still drive only the dead `backend` stack and aren't in the gate).
- **Two frontends:** `apps/frontend` (right home, fixture shell) vs `backend/src/frontend`
  (real wiring, doomed tree).

The five convergence tasks below close all of that. The Postgres serving tier (old M10)
**landed** — `RunRepository` + SQLite/Postgres behind M1's port are in
`packages/infra/storage`; further expansion is trigger-gated and needs no open task.

## In flight

| Who | Area / files | Claimed | Note |
|-----|--------------|---------|------|
| Claude (agent) | **C1**: `infra/actor/**`, `infra-ibkr/**`, `infra/pyproject.toml`, ADR 0025 | 2026-06-05 | **C1 IBKR-first increment LANDED (committed on `feat/merge-market-data`); Saxo/Deribit on standby per owner.** Adopted **Nautilus as the runtime spine** (ADR 0023): `nautilus_trader` is now a real `infra` dep; the M4 plane was already relocated in HEAD `3a21d9f`. **(A)** dep added, unused `pandas>=3.0.3` pin relaxed (no source imports pandas). **(B)** `infra/actor/nautilus_host.py` — a thin Nautilus `Actor` (`AnalyticsActor`) replays a `RawMarketEvent` stream through Nautilus's engine on its simulated clock and drives the **unchanged** pure `run_analytics`; `driver.py` stays `nautilus_trader`-free. Determinism gate `test_nautilus_replay_byte_identical.py` proves hosted == direct (ActorOutputs + persisted Parquet byte-identical, stamps incl.). **(C)** IBKR → Nautilus's InteractiveBrokers adapter to the **verifiable boundary**: CI-tested tick→`RawMarketEvent` normalizer (`content_event_id` restored) + import-guarded `build_data_client_config`; live connect needs a Gateway (skipped in CI). ib_async path superseded (kept for C5). **Decisions recorded in [ADR 0025](../.agent/decisions/0025-nautilus-host-catalog-topology.md):** our `RawMarketEvent`+`ParquetStore` stays the system of record (Nautilus bridges, ADR 0019 upheld); EventSource (0016) stays YAGNI. Gate: ruff/mypy/lint-imports clean in scope, all `packages/` tests green. **IBKR-REST course requirement — LANDED** ([ADR 0024](../.agent/decisions/0024-ibkr-rest-transport-alongside-tws.md) now **accepted**, owner ruled): custom Client Portal REST/WS adapter (`infra-ibkr/cp_rest_*`, httpx/websockets) normalizing into `RawMarketEvent` alongside the TWS path, `transport: rest|nautilus-tws` selector (REST default), `/tickle` keepalive, read-only proven, secdef search→strikes→info. Headline **REST↔TWS equivalence** test green (same observation → byte-identical events). Live CP Gateway unverifiable in CI (smoke on a Gateway host). **Still open:** Saxo/Deribit migration onto the runtime + the live `TradingNode`/collector wiring (the `transport` switch consumer) are later tasks. |
| Claude (agent) | `infra/{qc,validation}/**` + `tests/test_{qc_checks,qc_report,triage,validation,seam_triage}.py` | 2026-06-05 | **C2 LANDED (uncommitted), gate-green in isolation.** Ten named QC checks + the anomaly/triage validation plane ported into `packages/infra` under `algotrading.infra.*`. One persisted shape (`triage_records`), three sources (`qc`/`validation`/`anomaly`, discriminated off `reason_code` in one place); legacy in-memory `TriageRow` dropped. `check_collector_continuity` consumes the `qc.CollectorContinuityInput` Protocol — structural, so C1's eventual `CollectorSummary` satisfies it with no adapter (no edit to C1's plane). 97 tests pass; ruff + mypy (12 files) + import-linter (2/2) clean on C2. ADR 0010 updated. Pre-existing `apps/frontend` reds are unrelated. **Open for C3:** the `qc_job`/`validation_job` wiring. **Stale dupes for C5:** `backend/{qc,validation}`. |
| Claude (agent, for Anthony) | `apps/frontend/web/**` | 2026-06-05 | UI cleanup + theme pass: metric overflow, money formats, labelled vol surface, status labels. No BFF/Python changes. |
| Matthieu (Claude) | **C6** (DONE, uncommitted): `infra/{collectors,connectivity}/**`, `infra/orchestration/{jobs.py,surface_job.py,provider_flow.py,__init__.py}`, `infra-{saxo,deribit}` READMEs + 4 collectors/connectivity/orchestration READMEs + ADR 0027 | 2026-06-05 | **C6 COMPLETE, gate-green in my scope (ruff/mypy/lint-imports clean; 766 passed / 16 skipped). The only reds are C4-owned `apps/frontend` (8 ruff + 12 mypy + 3 tests), of which 1 mypy file + 1 test are the documented C6→C4 live-run reconciliation (below).** Unified the collection seam per ADR 0027: **one** `BrokerTick` (push EAV shape + `sequence`) writing the canonical `contracts.RawMarketEvent`; the one `RawCollector` is store-wired, idempotent (reload-seen + content-addressed `event_id`, proven against the real store), records gaps from `record_reconnect`. Retired the pull seam (deleted `connectivity/{broker,sessions}.py`, `contracts.broker.BrokerTick`/`BrokerSession`, `SessionSupervisor.stream()`/`SupervisedTick`); kept `SessionSupervisor` as the sole reconnect home (backoff/client-id/GapInterval/`recover`) + `content_event_id`. Ported the four use-cases onto the seam, **two documented skips removed**: `collect_live` (jobs.py), `surface_job.build_surface`, `provider_flow.run_provider_flow`, the handover smoke stage. live==replay extended to the live capture path (byte-identical raw on re-capture + identical derived). New tests: `test_collectors.py`, `test_collection_use_cases.py`, `test_session_supervisor.py`; rewired `test_broker_session.py`, `test_broker_agnostic.py`, `test_orchestration.py`, `test_handover_e2e.py`. **C4 reconcile (live-run path):** `apps/frontend/runner.py` calls the old `build_surface(selection=, supervisor=)`; the unified signature is `build_surface(adapter=, masters=, drive=, clock=)` — or call `collect_live` directly. Landing `build_surface` flips `_ORCHESTRATION_AVAILABLE`→True, so the 3 stale "orchestration_pending" frontend stub tests need updating (same as C3 did for 2 health tests; 2 of the 3 already failed on the C3 base). **Stale for C5:** `backend/{collectors,connectivity,orchestration/{surface_job,jobs}}`. |
| Matthieu (Claude) | **C3**: `infra/{orchestration,observability}/**` + READMEs + `infra/tests/test_{replay_byte_identical,provenance_verification,replay_reconstruction,orchestration,observability_runner,handover_e2e}.py` + ADR 0026 + `.agent/map.md` + 2 stale C4 health tests | 2026-06-05 | **C3 ENGINE SURFACE COMPLETE (uncommitted), gate green: 744 passed / 18 skipped / 1 pre-existing C4 SPX red (not mine). ruff + mypy(15) + lint-imports(2/2) clean.** One driver only — the actor; no second analytics path (ADR 0026). **Landed:** `orchestration/{jobs,qc_job,metrics(×5),alerts(×4),dashboard,run_state,storage_root,pipeline}` + `reconstruction/` subpackage; `observability/run_job` (run-lineage over the M10 `RunRegistry` — the one Vincent helper adopted). **All 4 headline acceptance tests relocated into the root gate, green on `packages/`:** byte-identical replay (live vs replay-off-disk, multi-underlying) + provenance + reconstruction robustness (missing≠empty, versioned restatement, replay==live) + handover **engine path** (bootstrap→reconstruct→QC). Plus test_orchestration (kill/restart idempotency, 5 metrics, 4 alerts, dashboard, reconciliation, run-state) + observability-lineage tests. Landing this flipped C4's pre-wired BFF health router from its `orchestration-pending` stub to the live `build_dashboard` path → updated 2 stale C4 stub tests to assert live behavior (router unchanged). ADR 0026 records which Vincent helpers were adopted (`run_job`) vs declined (archive/persist/positions_io/universe_io/risk_pipeline/compare — fork our storage spine or duplicate ours) vs deferred. **DEFERRED → C1 collection seam (the only thing left; needs a broker-session→`RawMarketEvent` bridge — pull `SessionSupervisor`/`contracts.BrokerTick` vs push `RawCollector`/EAV `collectors.BrokerTick`, owner-deferred):** `collect_live`, `surface_job`, the handover connectivity-smoke stage (b), `provider_flow`. Two documented `skip`s mark them; the EOD pipeline keeps collection as an injected seam. **Stale for C5:** `backend/orchestration` + migrated `backend/tests`. |
| Matthieu (Claude) | **C4**: `apps/frontend/**` (BFF Python + `web/`) | 2026-06-05 | **C4 DONE (branch `feat/c4-frontend`). Root gate green: ruff clean, mypy 173 files clean, lint-imports 2/2 kept, pytest 741 passed / 18 skipped / 0 failed; `cd apps/frontend/web && npm run lint && npm test` green (9 tests).** One frontend in `apps/frontend`, wired to the real `packages/infra` seams. **Done:** the six routers (health/surfaces/risk/run/config/oauth) read live infra — health off `orchestration.build_dashboard`, surfaces/risk read back from `ParquetStore`, oauth's CSRF half real (token exchange fails closed `501` pending infra-saxo); ported the canonical 7-page web app (Home/Health/Surfaces/Risk/Run/Config/NotFound, react-router + `AppLayout` + test helpers), dropping the Codex `Market`/`RiskScenarios`/`Orders` pages. **Codex extras dropped (recorded in commit):** `market`/`orders` routers + `data.py`/`store_serving.py` (~700 lines of synthesized fixtures, no backend equivalent) and their web pages/chart components. **Fixed:** runner's flat `fixtures.library` import + the nonexistent `orchestration.build_surface` import (16 mypy errors cleared); the failing SPX `test_market_api` resolved by dropping the market router (the AAPL→SPX selector default was that router's concern — default stays AAPL, the symbol the sample chain produces); 8 ruff import-ordering errors cleared. New `test_readback_api.py` exercises the real persist→read-back path (seed `surface_parameters`/`risk_aggregates`/`scenario_results` → routers read back). **Left for C6:** the SAMPLE live-run build path (`build_surface` starts with `collect_live`, owned by C6) is a clean stub with a `TODO(C6)` in `runner.py`; the job lifecycle is live, a SAMPLE run settles to ERROR with a typed C6-pending message. **Hands C5 wave 2:** `backend/src/frontend` + `backend/web` are now safe to delete. |
| Claude (tech-lead doc pass) | docs only: `.agent/{map,conventions,glossary}`, `.agent/decisions/{0023,0020,0022,0007,0008}`, `tasks/{TASKBOARD,C1-actor-and-market-data-plane}`, `documentation/{known-limitations,interface-contracts}`, `BIG_PICTURE.md`, `packages/infra*/**/README.md` | 2026-06-05 | Propagating the ADR 0023 direction (Nautilus spine + library-leverage + keep Saxo/Deribit) across every agent-read doc so no one builds the superseded plan. No code edits. |
| Claude (agent) | NEW docs only: `.agent/open-questions.md`, `documentation/vision-medium-term.md`, pointer added to `AGENTS.md` "Decisions" | 2026-06-05 | **Done.** Created the pending-decision register (`open-questions.md`, seeded OQ-1..4 + resolved OQ-0→ADR 0024) and the forward-looking medium-term vision (the index→constituents daily-snapshot pipeline: delta-band per tenor, IV/surface/Greeks decimal+dollar, parquet raw, daily close cron). **Follow-up for the doc-pass owner:** add `.agent/map.md` rows for both new docs (didn't touch `map.md` — you hold it). |
| Claude (agent) | NEW doc only: `documentation/configuration-and-reproducibility.md`; added OQ-5/OQ-6 to `.agent/open-questions.md` | 2026-06-05 | **Done.** The config & reproducibility architecture+standard (Theme B of the hygiene audit), anchored on **blueprint Part VII** (YAML taxonomy `environment/broker/universe/qc/scenarios/pricing` + inheritance) and Part I (versioning). Codifies: no business param as a `.py` literal; YAML → typed validated config (`from_config`+`__post_init__`+`version`) → DI into pure compute; the existing `config_hash`/`composite_config_hash`/`ProvenanceStamp` are the reproducibility mechanism (environment excluded); **profiles = the blueprint's config inheritance** (base+overlay+hash). Ends with the 5 fix-tasks (TOML→YAML, six base YAMLs, generalize the typed pattern, wire config into `infra`, stamp composite hash). **Open:** OQ-5 (`StorageRepository` port load-bearing vs delete) + OQ-6 (on-disk profile format). To be **ratified by a short ADR** on owner sign-off. **Follow-up for doc-pass owner:** link it from `conventions.md` + `.agent/map.md`. |

## Convergence workstreams

Six tasks close the merge. Each owns a disjoint set of directories and talks only
through the seams M0 already froze (`contracts`). Read each spec before starting;
read [TESTING.md](TESTING.md) before writing tests — code without the named tests is
not done.

| # | Task | Spec | Owns (dirs) | Depends on |
|---|------|------|-------------|------------|
| C1 | Market-data plane + actor spine in `packages`, broker fork resolved | [C1-actor-and-market-data-plane.md](C1-actor-and-market-data-plane.md) | `infra/{connectivity,collectors,universe,actor}`, `infra-{ibkr,saxo,deribit}` | M0, M1 (landed) |
| C2 | QC + validation/triage ported to `packages` | [C2-qc-validation.md](C2-qc-validation.md) | `infra/{qc,validation}` | M0 (landed) |
| C3 | Orchestration, observability + headline acceptance tests on the `packages` stack, in the gate | [C3-orchestration-and-acceptance.md](C3-orchestration-and-acceptance.md) | `infra/{orchestration,observability}` + acceptance tests | C1, C2 |
| C4 | Consolidate the two frontends into `apps/frontend` | [C4-frontend.md](C4-frontend.md) | `apps/frontend` | C1, C2, C3 (seams) |
| C6 | Unify the collection seam ([ADR 0027](../.agent/decisions/0027-collection-seam-push-canonical.md)) + port the 4 deferred use-cases + live-wire Saxo/Deribit | [C6-collection-seam-unification.md](C6-collection-seam-unification.md) | `infra/{collectors,connectivity}`, `infra/orchestration/{collect_live,surface_job,provider_flow}`, `infra-{saxo,deribit}` | ADR 0027 (accepted); C1/C3 (landed) |
| C5 | Retire the `backend/` flat tree | [C5-retire-backend.md](C5-retire-backend.md) | deletion of `backend/**` + doc truth | each module after its port (collection modules wait on C6) |

### Launch order

1. **C1 is the keystone** — the actor unblocks the entire top half (C3 can't run the
   headline tests without it). **C2 runs alongside C1** (it needs only M0).
2. **C3 after C1 + C2** — it drives the ported actor and moves the acceptance bar onto
   the `packages` stack and into the root gate.
3. **C6 unblocks the tail** — with ADR 0027 settling the collection seam (push `RawCollector`
   canonical; harvest `sequence`+`SessionSupervisor` from the pull seam, then retire it), C6
   unifies the seam and ports the four use-cases (`collect_live`, `surface_job`, the handover
   smoke stage, `provider_flow`) plus Saxo/Deribit live-wiring. This is what lets `backend/`'s
   collection + orchestration modules fully retire.
4. **C4** can port early against stubs; final run/health wiring needs the C6 live path.
5. **C5 is continuous** — retire M0–M3's stale `backend` dupes **now** (config, provenance,
   contracts, storage, snapshots, forwards, iv, surfaces, pricing, risk, fixtures) plus
   qc/validation (C2), actor/connectivity/collectors/universe (C1), orchestration engine (C3);
   the collection-coupled modules retire the moment C6 lands them green. Done when there is no
   `backend/` and the root gate is the only gate.

## Open future spikes (not part of convergence)

- [ibkr-rest-api-evaluation.md](ibkr-rest-api-evaluation.md) — **REST is now a course
  requirement** (no longer a spike). It triggers exactly the "unless Nautilus's IBKR coverage
  proves insufficient" caveat from [ADR 0023](../.agent/decisions/0023-nautilus-runtime-spine-and-library-leverage.md):
  Nautilus's IBKR adapter is TWS/Gateway, so it does not meet the REST requirement. Proposed
  resolution in **[ADR 0024](../.agent/decisions/0024-ibkr-rest-transport-alongside-tws.md)
  (status: proposed)** — IBKR-over-REST as a custom adapter into the Nautilus catalog (the
  Saxo/Deribit pattern), Nautilus-TWS as a config-flip fallback. **Needs an owner ruling** (is
  this an accepted exception to ADR 0023?) and is sequenced after C1 owns the catalog seam.

## Format

`| your-name-or-agent | infra/foo/... | 2026-06-05 | short intent |`
