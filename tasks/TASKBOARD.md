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

We merged two independent builds of the same system — this repo and Vincent's
(`github.com/Vincent-20-100/AlgoTrading` @ `refactor/audit-remediation`) — toward
the max-union of both. Two decisions are **locked**: keep our framework-free
**actor** as the spine, and adopt Vincent's **layered uv-workspace monorepo** as the
chassis. Vincent's repo is checked out read-only at **`Vincent's Code/`** (gitignored,
not canonical) as a source of inspiration; refresh with `git -C "Vincent's Code" pull`.

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
- **Forked:** the broker workstream vendored a parallel copy of Vincent's
  collector/universe slice into `packages/infra`, creating a second `BrokerTick` and a
  selection-less universe. This is the only red on the root gate (3 Saxo-config tests).
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
| Claude (agent) | **C1**: `infra/{actor,connectivity,collectors,universe}/**`, `infra-{ibkr,saxo,deribit}/**`, new ADR 0023 | 2026-06-05 | **C1 in flight.** Resolving the live M4-vs-M5 fork: relocate the canonical M4 plane + framework-free actor from `backend/src` into `algotrading.infra`, delete the M5 vendored fork (2nd `BrokerTick`, selection-less universe, lifecycle `BrokerSession`), retarget the 3 leaves onto the frozen `contracts.BrokerSession` + the one `chain_planning` policy. 3 sequenced commits (relocate → retarget+delete-fork+ADR → tests+docs), gate green at each. Supersedes ADR 0022, closes the ADR 0020 contest. `backend/{actor,connectivity,collectors,universe}` become stale dupes → C5. |
| Claude (agent) | `infra/{qc,validation}/**` + `tests/test_{qc_checks,qc_report,triage,validation,seam_triage}.py` | 2026-06-05 | **C2 LANDED (uncommitted), gate-green in isolation.** Ten named QC checks + the anomaly/triage validation plane ported into `packages/infra` under `algotrading.infra.*`. One persisted shape (`triage_records`), three sources (`qc`/`validation`/`anomaly`, discriminated off `reason_code` in one place); legacy in-memory `TriageRow` dropped. `check_collector_continuity` consumes the `qc.CollectorContinuityInput` Protocol — structural, so C1's eventual `CollectorSummary` satisfies it with no adapter (no edit to C1's plane). 97 tests pass; ruff + mypy (12 files) + import-linter (2/2) clean on C2. ADR 0010 updated. Pre-existing `apps/frontend` reds are unrelated. **Open for C3:** the `qc_job`/`validation_job` wiring. **Stale dupes for C5:** `backend/{qc,validation}`. |
| Claude (agent, for Anthony) | `apps/frontend/web/**` | 2026-06-05 | UI cleanup + theme pass: metric overflow, money formats, labelled vol surface, status labels. No BFF/Python changes. |
| Claude (agent, for Anthony) | `apps/frontend/{src,tests}/**` (BFF Python only — web/ untouched, see line above) + `backend/scripts/sample_day.py` (new, throwaway) + `data/` | 2026-06-05 | **C4 slice in flight** (the part not blocked on C1–C3): produce a SAMPLE day into `data/` via the backend pipeline's public entries (script dies with C5), then serve the operator BFF's market/risk routes from the real tables via `algotrading.infra` storage/pricing/risk seams only (schemas byte-identical to flat `contracts`); scenario POST reprices live through the frozen pricing seam. Routes/shapes unchanged; underlyings not in the store keep the explicit fixture stamp (no silent mixing). No edits in `backend/src/**`; run/health/config/oauth routers wait for C3. |
| Codex | `Test Lenny/**` only | 2026-06-05 | Standalone IBKR paper-trading volatility dashboard prototype; no edits to existing app/backend code. |

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

- [ibkr-rest-api-evaluation.md](ibkr-rest-api-evaluation.md) — evaluate replacing the
  IBKR TWS-API/Gateway transport with the REST API, behind the same `BrokerSession` seam.
  A spike, not a blocker; pick up after C1 lands the broker leaves.

## Format

`| your-name-or-agent | infra/foo/... | 2026-06-05 | short intent |`
