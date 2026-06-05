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
| Matthieu (Claude) | **C3**: `infra/{orchestration,observability}/**` + acceptance tests in `infra/tests` + root `pyproject.toml` testpaths | 2026-06-05 | Port orchestration/observability around the **one** actor; relocate the 3 headline tests (`replay_byte_identical`, `provenance_verification`, `handover_e2e`) onto the `packages/` stack and into the root gate. First increment: the two pure headline tests (byte-identical replay + provenance) relocated to `algotrading.infra.*`. |
| Claude (agent, for Anthony) | `apps/frontend/{src,tests}/**` (BFF Python only — web/ untouched, see line above) + `backend/scripts/sample_day.py` (new, throwaway) + `data/` | 2026-06-05 | **C4 slice in flight** (the part not blocked on C1–C3): produce a SAMPLE day into `data/` via the backend pipeline's public entries (script dies with C5), then serve the operator BFF's market/risk routes from the real tables via `algotrading.infra` storage/pricing/risk seams only (schemas byte-identical to flat `contracts`); scenario POST reprices live through the frozen pricing seam. Routes/shapes unchanged; underlyings not in the store keep the explicit fixture stamp (no silent mixing). No edits in `backend/src/**`; run/health/config/oauth routers wait for C3. |
| Claude (tech-lead doc pass) | docs only: `.agent/{map,conventions,glossary}`, `.agent/decisions/{0023,0020,0022,0007,0008}`, `tasks/{TASKBOARD,C1-actor-and-market-data-plane}`, `documentation/{known-limitations,interface-contracts}`, `BIG_PICTURE.md`, `packages/infra*/**/README.md` | 2026-06-05 | Propagating the ADR 0023 direction (Nautilus spine + library-leverage + keep Saxo/Deribit) across every agent-read doc so no one builds the superseded plan. No code edits. |

## Convergence workstreams

Five tasks close the merge. Each owns a disjoint set of directories and talks only
through the seams M0 already froze (`contracts`). Read each spec before starting;
read [TESTING.md](TESTING.md) before writing tests — code without the named tests is
not done.

| # | Task | Spec | Owns (dirs) | Depends on |
|---|------|------|-------------|------------|
| C1 | Market-data plane + actor spine in `packages`, broker fork resolved | [C1-actor-and-market-data-plane.md](C1-actor-and-market-data-plane.md) | `infra/{connectivity,collectors,universe,actor}`, `infra-{ibkr,saxo,deribit}` | M0, M1 (landed) |
| C2 | QC + validation/triage ported to `packages` | [C2-qc-validation.md](C2-qc-validation.md) | `infra/{qc,validation}` | M0 (landed) |
| C3 | Orchestration, observability + headline acceptance tests on the `packages` stack, in the gate | [C3-orchestration-and-acceptance.md](C3-orchestration-and-acceptance.md) | `infra/{orchestration,observability}` + acceptance tests | C1, C2 |
| C4 | Consolidate the two frontends into `apps/frontend` | [C4-frontend.md](C4-frontend.md) | `apps/frontend` | C1, C2, C3 (seams) |
| C5 | Retire the `backend/` flat tree | [C5-retire-backend.md](C5-retire-backend.md) | deletion of `backend/**` + doc truth | each module after its port |

### Launch order

1. **C1 is the keystone** — the actor unblocks the entire top half (C3 can't run the
   headline tests without it). **C2 runs alongside C1** (it needs only M0).
2. **C3 after C1 + C2** — it drives the ported actor and moves the acceptance bar onto
   the `packages` stack and into the root gate.
3. **C4** can port early against stubs; final wiring needs the C1–C3 seams.
4. **C5 is continuous** — retire M0–M3's stale `backend` dupes now; retire each remaining
   module the moment its convergence task lands it green. Done when there is no `backend/`
   and the root gate is the only gate.

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
