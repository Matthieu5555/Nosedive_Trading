# M0 — Monorepo keystone (skeleton, core layer, frozen seams, gate)

- **Branch:** `feat/merge-keystone`
- **Owns:** the workspace root (`pyproject.toml`, `uv.lock`, `.pre-commit-config.yaml`, import-linter config, CI under `.github/`), `packages/core/**`, and the *frozen contract surfaces* every other workstream imports.
- **Depends on:** nothing. **This is the keystone — it lands first and blocks everyone.**
- **Blocks:** M1–M9.

## The merge context (read once)

We are merging two independent builds of the same system: this repo (`/srv/project`, flat `backend/src`, Nautilus actor spine, `.agent/` discipline) and Vincent's (`github.com/Vincent-20-100/AlgoTrading` @ `refactor/audit-remediation`, a layered uv-workspace monorepo, multi-broker, with a frontend). Two architectural decisions are **locked** and not to be re-litigated:

1. **Chassis = Vincent's layered monorepo.** `core → infra → strategy → execution → frontend`, "a layer never imports a layer above it", enforced mechanically by **import-linter**, one uv workspace / one lock.
2. **Spine = our Nautilus actor.** Drop Vincent's hand-rolled `orchestration/pipeline` as the driver. The same actor runs live and replay; broker transports feed it (see M4/M5).

**Working assumption — confirm with the user if unsure:** the merged monorepo is built **in place in `/srv/project`** (we restructure ours into the layout and port Vincent's packages in), keeping our `AGENTS.md`/`.agent/` discipline and Nautilus spine. If the user instead wants to work inside Vincent's repo or a fresh repo, only the physical location changes — the decomposition below is identical.

## Objective

Stand up the empty-but-enforced chassis and freeze every cross-package seam, so M1–M9 can fan out without colliding and without importing each other's internals. You write almost no domain logic; you write the *shape* and the *contracts*.

## What you build

1. **Workspace skeleton.** The uv workspace with one package per layer — model it on Vincent's `pyproject.toml` + `packages/*/pyproject.toml` layout. Target tree:
   ```
   packages/core/src/algotrading/core
   packages/infra/src/algotrading/infra/{contracts,storage,snapshots,forwards,iv,surfaces,pricing,risk,qc,validation,collectors,connectivity,universe,actor,orchestration,observability,utils}
   packages/infra-ibkr  packages/infra-saxo  packages/infra-deribit
   packages/strategy  packages/execution      (skeletons)
   apps/frontend                              (skeleton)
   ```
   Most `infra` subpackages land empty here with just `__init__.py` + a stub README; their owners (M1–M7) fill them. Note `actor/` is new vs Vincent — that is our spine's home.

2. **Layering enforcement.** import-linter contracts encoding `core ← infra ← {strategy,execution} ← frontend` and forbidding the reverse, plus "infra is blind to alpha" (infra must not import strategy/execution/frontend). This runs in the gate. Port Vincent's config; extend it for the broker packages (`infra-*` may import `infra` + `core`, nothing above).

3. **The `core` layer (first real bake-off).** Merge our `backend/src/{config,provenance}` and contracts with Vincent's `packages/core/src/algotrading/core/{config,log,manifest,provenance}`. Keep the better of each: our provenance stamp + validate, his structured `log` + `manifest`. The output `core` owns config loading, structured logging, and the provenance stamp — domain-agnostic, light deps.

4. **The frozen contract surface (the seam everyone imports).** Merge our `backend/src/contracts/{instrument_key,bundles,tables,registry,validation}` with Vincent's `infra/universe/contracts.py` + `infra/storage/schema.py`. Freeze and publish: the instrument key, the typed table/bundle dataclasses, and the **two protocols** the merge hinges on —
   - **`StorageRepository` port** (read/write/list raw + derived, versioned restatement) — model on Vincent's `infra/storage/ports.py`, reconciled with our versioned-partition semantics (see M1).
   - **`BrokerSession` protocol** (connect/subscribe/option-chain/ticks, broker-agnostic) — our `backend/src/connectivity/broker.py`, the seam M5's adapters implement and M4's actor drives.
   These two protocols must be frozen here before M1/M4/M5 start, exactly as Workstream A's contracts were frozen first in the original build.

5. **The merged quality gate + CI.** One command, green on the empty skeleton: `uv run ruff check . && uv run mypy . && uv run import-linter && uv run pytest -q`. Wire Vincent's `.pre-commit-config.yaml`, `.github/workflows/` (incl. the public-safety/secrets scanner — keep it), and the author/skills-contract checks. Update `AGENTS.md`'s "Verify before you declare done" section to this command.

## Frozen seam (what you hand the others)

`algotrading.core` (config/log/provenance), `algotrading.infra.contracts` (instrument key, tables, bundles), `StorageRepository` and `BrokerSession` protocols, the analytics dataclass contracts placeholder, and a green gate. Publish them in `documentation/interface-contracts.md` (ported from ours). Once tagged frozen, a change here is a cross-cutting event that pings every in-flight workstream on the board.

## Test surface

Read [TESTING.md](../TESTING.md) first. Specific to M0:
- import-linter actually fails on a planted upward import (assert the guard bites, don't trust the config).
- A round-trip test for each frozen protocol against a trivial in-memory fake, so M1/M4/M5 build against a proven contract.
- The provenance stamp survives the core bake-off: an existing stamp from either repo still validates.

## Done criteria

The skeleton imports, the layering guard is enforced and tested, `core` + the frozen contracts are merged and published, and the full gate (ruff/mypy/import-linter/pytest) is green on the empty tree. M1–M9 can `uv sync` and start against frozen seams.

## Gotchas

Do not pull domain logic up into the keystone — your job is the shape and the contracts, not the math. Freeze the two protocols *before* announcing M0 done; a late protocol change is the one thing that forces rework across every other stream. Keep our `.agent/`/`AGENTS.md` as the canonical steering (M9 reconciles Vincent's `.meta/` into it) — don't let two steering systems coexist unreconciled.
