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
| Claude (agent) | `backend/src/{qc,validation}` + additive `contracts` TriageRecord table | feat/integration-ops | 2026-06-05 | M6 **landed (flat layout)**: kept our 10 QC checks, added `validation` anomaly/triage sibling, collapsed both planes into one `triage_records` table. Gate green. ADR 0010. Awaiting M0 relocation under `packages/infra`. |
| Claude (agent) | `backend/tests/test_{replay_byte_identical,provenance_verification}.py` | feat/integration-ops | 2026-06-05 | M7 prep: harden the two headline acceptance tests to multi-underlying in current flat layout (per user; M0 relocates later) |
| Claude (agent) | workspace root, `packages/core`, `packages/infra/{contracts,storage}` | feat/integration-ops | 2026-06-05 | **M0 + M1 landed** (no branch, per user; blueprint = main rail). M0: layered uv-workspace + merged `core` + frozen `contracts` seam (`StorageRepository`/`BrokerSession`) + import-linter gate (ADR 0018). M1: `ParquetStore` analytics data plane behind the port, our versioning bug-fix preserved, one immutable flat-EAV raw model (ADR 0019). Both gate-green in isolation (storage: 11 files mypy-clean, 21 tests). M2/M3/M4 owned elsewhere in parallel. |
| Claude (agent) | `packages/infra/src/algotrading/infra/{snapshots,forwards,iv,surfaces,pricing,utils}` + their `packages/infra/tests/` | feat/merge-frontend (current, no dedicated branch per user) | 2026-06-05 | **M2 analytics bake-off** in flight. Verdict: utils=Vincent (single daycount/robust source); snapshots/forwards/iv/surfaces/pricing=ours, porting winners from `backend/src` onto frozen seam. Adopt Vincent's surfaces/diagnostics + golden fixtures + end-to-end test, iv structured reason codes. Freeze pricing interface for M3. Blueprint = absolute reference; oracle decides number disputes. |
| Claude (agent) | `packages/infra/src/algotrading/infra/risk/**` + `packages/infra/tests/test_{risk,scenario,risk_properties,seam_risk,determinism_risk}.py` + `tests/fixtures/positions.py` + golden | feat/merge-frontend (current, no dedicated branch per user) | 2026-06-05 | **M3 risk engine LANDED, gate-green.** Started as a structural port (M2/M1 absent) but M2 pricing (line 38) + M1 storage (line 37) landed in parallel, so it is now fully verified: **68 risk tests pass, mypy + ruff + import-linter clean**, determinism golden matches. Blueprint-driven bake-off: our verified core (bumps/greeks/valuation/scenario version-hash/recon non-finite guard) projecting into the M0-frozen `RiskAggregate`/`ScenarioResult` contracts (these match the blueprint data dictionary; Vincent's in-module shapes do not), + Vincent's additive surface (versioned `RiskSnapshot`, scenario report attribution, positions/basket Eq.23/config grouping). Binds the `algotrading.infra.pricing` seam (`PricingState`/`from_spot`/`price`/`PriceGreeks`) M2 froze. README + ADR 0006. |
| Claude (agent) | `backend/src/{universe,connectivity,collectors,actor}` (M4 merge content) + `.agent/decisions/0020-*` | feat/merge-market-data | 2026-06-05 | M4 market-data/actor: "keep ours" plane already green in flat layout. Landing the remaining merge content — reconcile chain-selection into one policy (add the capture/subscription stage from Vincent's `subscription`/`strike_selection` over our `ChainSelection`); freeze the adapter-to-actor seam for M5 (ADR 0020: raw-layer-replay wiring per blueprint, no Nautilus framework, consolidating 0003/0007/0016). Flat layout per repo convention; M1 storage has now landed in `packages/infra` (board line 37), so the plane's relocation is unblocked — deferred to the M0 move per convention. |
| Claude (agent) | `backend/src/frontend/**`, `backend/web/**` + `httpx` dev dep | feat/merge-frontend | 2026-06-05 | M8 frontend: FastAPI BFF + React/Vite web, built in flat backend (per user; M0 relocates to `apps/frontend` later). Wired to our flat `backend/src` seams (ParquetStore reads, surfaces/risk, `build_dashboard`); live broker/OAuth network paths implemented to the verifiable boundary, full wiring deferred until `packages/infra-saxo` lands. Self-contained under `backend/src/frontend` — no overlap with `packages/**` or `apps/frontend`. |
| Codex (agent) | `apps/frontend/**` | feat/merge-frontend-operator | 2026-06-05 | M8 operator frontend: contract-first FastAPI BFF + React/Vite pages for market snapshots/options/greeks/vol surface, risk scenarios, and orders/history. |
| Claude (agent) | `research/dispersion-eurostoxx50.md` (new, additive) | feat/integration-ops | 2026-06-05 | Research-direction note: implied correlation / dispersion on SX5E as the next demo. Doc only, no code — no overlap with anyone. |
| Claude (agent) | `apps/frontend/**` | feat/merge-frontend-operator | 2026-06-05 | M8 operator frontend follow-up: greeks charts on Risk page (spot ladder + expiry buckets, additive ScenarioResult fields), shared format/Metric cleanup, useFetch stale-while-refetch fix. |
| Claude (agent) | `packages/infra-{ibkr,saxo,deribit}/**` + vendored M4/M1 slice in `packages/infra/{collectors,universe,connectivity}` (new files) + additive `storage/{events,json_io}.py` | feat/merge-postgres (current, no dedicated branch per user) | 2026-06-05 | **M5 broker adapters**, per **explicit workspace-owner direction to vendor Vincent's collector/universe slice near-verbatim**. ⚠️ **Knowingly contests accepted ADR 0020** (froze the M5 seam to M0's thin `contracts.BrokerSession`; said *not* to vendor these as a parallel module) and **overlaps M4's claimed dirs** (line above-ish, `feat/merge-market-data`, building in `backend/src`, relocation to `packages/infra` pending). Conflict is intentional, recorded in **ADR 0021** so it surfaces to the M4 owner as a visible merge decision, not a silent overwrite. infra edits additive; brokers minus `flow.py` (needs absent analytics pipeline). |

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
