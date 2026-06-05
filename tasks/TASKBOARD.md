# TASKBOARD

The in-repo collision guard for a shared workspace with several humans and agents
working at once. **Before you start changing files, claim them here.** Clear your
claim when you're done. This is advisory, not enforced — it only works if every
actor reads and writes it, which is why `AGENTS.md` tells you to. The real safety
is branch discipline (one branch per task, merge small and often); the board is
the early warning that two of you are about to collide.

When a task is finished, move its line to `tasks/archive/` with a one-line note on
what was done, so "why was this changed" stays answerable later.

## Current phase: the merge

We are merging two independent builds of the same system — this repo and Vincent's
(`github.com/Vincent-20-100/AlgoTrading` @ `refactor/audit-remediation`) — toward
the max-union of both. Two decisions are **locked**: keep our **Nautilus actor** as
the spine, and adopt Vincent's **layered uv-workspace monorepo** as the chassis.
See `M0-monorepo-keystone.md` for the full context.

Vincent's repo is checked out locally at **`Vincent's Code/`** (repo root, on
`refactor/audit-remediation`) as a read-only source of inspiration — gitignored,
not part of the canonical workspace. Every `packages/…` path the M-tasks cite (e.g.
`packages/infra/src/algotrading/…`) is relative to that folder. Refresh it with
`git -C "Vincent's Code" pull`.

The original five-workstream backbone build (A–E) is **complete**; its specs are in
`tasks/archive/` and its uncommitted `feat/integration-ops` work should be committed
before the restructure begins.

## In flight

| Who | Area / files | Branch | Claimed | Note |
|-----|--------------|--------|---------|------|
| Claude (agent) | `AGENTS.md`/`.agent/**`, `documentation/**`, `.claude/skills/**`, `notebooks/**` | feat/merge-discipline | 2026-06-05 | M9: fold Vincent's blueprint + vol-surface docs + notebooks; reconcile ADR stream 0011–0016; merge glossary (crypto/Deribit/Saxo); update map.md. |
| Claude (agent) | `backend/src/{qc,validation}` + additive `contracts` TriageRecord table | feat/integration-ops | 2026-06-05 | M6 **landed (flat layout)**: kept our 10 QC checks, added `validation` anomaly/triage sibling, collapsed both planes into one `triage_records` table. Gate green. ADR 0010. Awaiting M0 relocation under `packages/infra`. |
| Claude (agent) | `backend/tests/test_{replay_byte_identical,provenance_verification}.py` | feat/integration-ops | 2026-06-05 | M7 prep: harden the two headline acceptance tests to multi-underlying in current flat layout (per user; M0 relocates later) |
| Claude (agent) | workspace root, `packages/**`, `apps/frontend` — M0→M4 restructure | feat/integration-ops | 2026-06-05 | Monorepo restructure in place (no branch, per user). Blueprint = main rail. M0 keystone, then M1 storage, M2 analytics, M3 risk, M4 market-data/actor, in order. |
| Claude (agent) | `backend/src/frontend/**`, `backend/web/**` + `httpx` dev dep | feat/merge-frontend | 2026-06-05 | M8 frontend: FastAPI BFF + React/Vite web, built in flat backend (per user; M0 relocates to `apps/frontend` later). Wired to our flat `backend/src` seams (ParquetStore reads, surfaces/risk, `build_dashboard`); live broker/OAuth network paths implemented to the verifiable boundary, full wiring deferred until `packages/infra-saxo` lands. Self-contained under `backend/src/frontend` — no overlap with `packages/**` or `apps/frontend`. |
| Claude (agent) | `research/dispersion-eurostoxx50.md` (new, additive) | feat/integration-ops | 2026-06-05 | Research-direction note: implied correlation / dispersion on SX5E as the next demo. Doc only, no code — no overlap with anyone. |

## Format

`| your-name-or-agent | packages/foo/... | feat/merge-foo | 2026-06-05 | short intent |`

## Merge workstreams

Ten orthogonal workstreams, one agent each, each owning a disjoint set of
directories and talking only through the seams **M0 freezes first**. M0 is the
keystone (monorepo skeleton + `core` + the frozen contracts/protocols + the gate);
it lands before everything else, exactly as Workstream A did in the original build.
The rest fan out. M7 converges last and verifies the headline invariants.

Before writing tests in any workstream, read [TESTING.md](TESTING.md) — the shared
test-surface contract (independent oracles, the determinism mechanism, seam/contract
tests, property tests, coverage floors). Each spec's own **Test surface** names the
cases specific to its modules. Code without the named tests is not done.

| # | Workstream | Spec | Branch | Owns (dirs) | Depends on |
|---|------------|------|--------|-------------|------------|
| M0 | Monorepo keystone — skeleton, `core`, frozen seams, gate/CI | [M0-monorepo-keystone.md](M0-monorepo-keystone.md) | feat/merge-keystone | workspace root, `packages/core`, frozen `contracts` + `StorageRepository`/`BrokerSession` protocols | — (keystone) |
| M1 | Storage — raw/curated, ports, EAV, tiered stores | [M1-storage.md](M1-storage.md) | feat/merge-storage | `infra/storage` | M0 |
| M2 | Analytics core — snapshots/forwards/iv/surfaces/pricing | [M2-analytics-core.md](M2-analytics-core.md) | feat/merge-analytics | `infra/{snapshots,forwards,iv,surfaces,pricing,utils}` | M0 |
| M3 | Risk engine — greeks/aggregation/scenarios/reconciliation | [M3-risk-engine.md](M3-risk-engine.md) | feat/merge-risk | `infra/risk` | M0, M2 |
| M4 | Market-data plane + Nautilus actor spine | [M4-market-data-actor.md](M4-market-data-actor.md) | feat/merge-market-data | `infra/{connectivity,collectors,universe,actor}` | M0, M1 |
| M5 | Broker adapters — IBKR / Saxo / Deribit | [M5-broker-adapters.md](M5-broker-adapters.md) | feat/merge-brokers | `infra-ibkr`, `infra-saxo`, `infra-deribit` | M0, M4 |
| M6 | QC + validation/triage | [M6-qc-validation.md](M6-qc-validation.md) | feat/merge-qc | `infra/{qc,validation}` | M0 |
| M7 | Orchestration, observability, replay, acceptance | [M7-orchestration-observability.md](M7-orchestration-observability.md) | feat/merge-orchestration | `infra/{orchestration,observability}` + acceptance tests | M0,M1,M2,M3,M4,M6 |
| M8 | Frontend — FastAPI BFF + React/Vite web | [M8-frontend.md](M8-frontend.md) | feat/merge-frontend | `apps/frontend` | M2,M3,M7 (API contract early) |
| M9 | Discipline + docs — steering, blueprint, notebooks | [M9-discipline-docs.md](M9-discipline-docs.md) | feat/merge-discipline | `AGENTS.md`/`.agent`, `documentation`, `.claude/skills`, `notebooks` | — (continuous) |
| M10 | **Postgres for the serving/metadata tier** — _conditional, future_ | [M10-postgres-serving-tier.md](M10-postgres-serving-tier.md) | feat/merge-postgres | metadata/serving stores behind M1's port (run registry, positions/risk/triage/universe) | M1, M8 — **do not start until a trigger fires** |

### Launch order

1. **M0 alone first** — nothing else can start until the skeleton + the two
   protocols (`StorageRepository`, `BrokerSession`) + analytics/pricing contracts
   are frozen and the gate is green.
2. **Then fan out:** M1, M2, M6, M9 depend only on M0 and start together. M3
   follows M2's frozen pricer; M4 follows M1; M5 follows M4's adapter seam.
3. **M7 converges last** and proves byte-identical replay + provenance end to end.

M9 is docs/steering only and runs continuously alongside the rest. Each workstream
stages only its own directories; the only shared edits are the frozen seams, which
M0 owns — change one and you ping every claim on this board.
