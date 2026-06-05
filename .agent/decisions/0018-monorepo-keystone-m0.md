# 0018 — Monorepo keystone (M0): layered uv-workspace, merged core, frozen seams

- **Status:** accepted. **Update 2026-06-05:** §3's frozen *pull* `contracts.BrokerSession`
  seam ("M5's adapters satisfy it, M4's actor drives it") is **superseded by
  [[0027-collection-seam-push-canonical]]** — the live adapters speak the push
  `MarketDataAdapter`/`RawCollector`; the scalar pull seam is retired. The other frozen seam
  (`StorageRepository`) and the workspace layering stand (the port is kept load-bearing per
  [[0028-configuration-and-reproducibility-standard]] / OQ-5).
- **Date:** 2026-06-05
- **Scope:** M0 — the keystone every other merge workstream builds on.
- **Relates to:** [[0001-workspace-layout]], [[0011-blueprint-as-plan-of-record]],
  [[0012-per-broker-leaf-packages]], [[0015-storage-repository-port-tiered-backends]],
  [[0016-eventsource-seam-backtest-readiness]], [[0017-provider-dimension]].

## Context

We are merging two independent builds of the same volatility platform — this repo
(flat `backend/src`, Nautilus actor spine, `.agent/` discipline) and Vincent's
(layered uv-workspace monorepo, multi-broker, frontend). Two decisions were already
locked: keep our Nautilus actor as the spine, adopt the layered uv-workspace as the
chassis. The blueprint (`industrial_roadmap…v4.pdf`, transcribed under
`Vincent's Code/packages/infra/docs/blueprint/`) is the canonical domain reference and
the plan of record — it adjudicates every merge decision, not an "ours vs his"
preference. M0 stands up the empty-but-enforced chassis and freezes every cross-package
seam, so M1–M9 fan out without colliding.

Built **in place** in `/srv/project`, on `feat/integration-ops` with **no new branch**
(per the workspace owner). Vincent's repo is reference *for code to port only* — its
process/steering layer (`.meta/`, `.claude/rules`, branching, skills-contract) is **not**
adopted; our `AGENTS.md` / `.agent/` stays the single source of truth.

## Decision

1. **Layered virtual uv-workspace.** One package per layer under `packages/` plus the
   cross-package app under `apps/frontend`, one venv / one lock. Layering
   `core ← infra ← {infra-<broker>} ← {strategy,execution} ← frontend` is enforced
   mechanically by **import-linter** (two contracts: the layers rule and "infra is blind
   to alpha"). Native-namespace packages (`algotrading/` has no `__init__`; each layer is
   `algotrading.<layer>`), PEP 561 `py.typed` markers so the packages type-check as typed.

2. **`core` bake-off, adjudicated by the blueprint.** Merged foundation:
   - **config** — kept *our* typed `PlatformConfig` (the blueprint, Part I "Core naming
     conventions", mandates versioning *every* configuration set: universe / QC / solver /
     scenario — our four versioned sections are the faithful implementation), and folded in
     Vincent's additive pieces: `composite_config_hash` and the generic versioned-YAML
     overlay loader (`load_yaml_config` → `LoadedConfig`) for free-form config bundles.
   - **provenance** — kept *our* stamp + `validate_stamp` (the determinism + lineage
     mechanism; canonical-JSON SHA-256, order-independent, cross-process stable), plus
     Vincent's `code_version` distribution helper.
   - **log / manifest** — adopted Vincent's structured JSON `get_logger` and `Manifest`.

3. **Frozen contract seam** (`algotrading.infra.contracts`) — ported our typed table
   contracts, instrument key, registry, and write-ahead validation, fixing imports to
   `algotrading.core`. Published the two protocols the merge hinges on:
   - **`StorageRepository`** (`contracts/ports.py`) — the analytics *data-plane* port:
     table-keyed read/write/list over the contract dataclasses, with the
     versioned-restatement semantics (`version=None` = live; `version=<V>` = one
     restatement; the two never mix; raw append-only tables refuse a versioned write).
     This is the blueprint's "one columnar partitioned store for raw and derived datasets"
     (Part I). It is **orthogonal** to the metadata/serving store — the blueprint's
     "relational metadata store" — whose port is `storage.ports.RunRepository` (M10,
     [[0015-storage-repository-port-tiered-backends]]). One analytics port + one metadata
     port, by store, is the blueprint-faithful shape; a single monolith would flatten the
     deliberate tier separation (Part XV retention tiers), and four micro-ports would
     fragment the one columnar store.
   - **`BrokerSession`** (`contracts/broker.py`) — the broker-agnostic market-data seam
     (`BrokerTick` + connect/subscribe/option-chain/ticks; `content_event_id` for
     deterministic, idempotent event ids). M5's adapters satisfy it, M4's actor drives it.

4. **The gate** is the root command in `AGENTS.md`:
   `uv run ruff check . && uv run mypy . && uv run lint-imports && uv run pytest -q`.
   Scoped to `packages/**` + `apps/**`; excludes `backend/`, the reference checkout, and
   notebooks. Optional/lazy SDK deps (broker `ib_async`, `psycopg`) carry mypy overrides.

## Test surface (M0-specific, all green)

- The layering guard **bites**: a planted `infra → strategy` import makes `lint-imports`
  fail (asserted by running the real linter, not trusting the config).
- Both frozen protocols round-trip against a trivial in-memory fake (`StorageRepository`
  versioned-restatement coexistence + append-only refusal; `BrokerSession` drive +
  idempotent event id), so M1/M4/M5 build against a proven contract.
- The provenance stamp survives the bake-off (a built stamp validates; order-independent;
  tamper-detected) and the config/stamp hashes are stable across processes (no
  `PYTHONHASHSEED` dependence).

## Consequences

M1–M9 can `uv sync` and start against frozen seams. A change to `contracts/` is a
cross-cutting event routed through M0. The flat `backend/` tree is retired workstream by
workstream as each ports its modules into `packages/infra/`. Built in a live shared tree
alongside parallel agents (M8 frontend, M10 metadata tier); the frozen seams are the only
shared edits, exactly as intended.
