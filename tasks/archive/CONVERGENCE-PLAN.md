# Convergence plan — the linear runbook

This is the A-to-Z list. Work it top to bottom. Each step assumes every step above
it is done and the root gate is green. The detailed *why* and *frozen seams* for the
lettered convergence tasks live in their own specs (`C4`, `C5`, `C6`) and the ADRs;
this file is the **order of operations** and the housekeeping that the per-task specs
don't own.

The bar we are building toward, in the owner's words: a clean repo, the minimal
amount of code needed, every module deeply implemented and **deeply tested at the
level of the contracts between modules**. Steps 1–8 close the merge and leave one
tree; steps 9–11 are the solidity bar that makes it the repo the owner asked for.

Ground truth at the time this plan was written (root gate, run 2026-06-05): ~744
tests pass, ~18 skip, **1 fails**, and that one failure plus all 16 mypy errors and
the 8 ruff errors are concentrated in two known places — the uncommitted C3 tree
(ruff) and the in-flight C4 frontend (mypy + the failing test). lint-imports is
clean. The bottom half (core, contracts, storage, analytics, risk), C1 (IBKR on
Nautilus + the actor + IBKR-REST), and C2 (QC/validation) are committed and green.
C3 (orchestration/observability + the four headline acceptance tests in the gate) is
complete but **uncommitted**.

---

## 1. Land C3

The largest body of green work in the repo is currently unsaved. Save it before
anything else.

- Run `uv run ruff check --fix .` to clear the 7 auto-fixable import-ordering errors,
  then resolve the 8th by hand.
- Commit the entire untracked orchestration/observability/acceptance-test tree
  (`infra/orchestration/**`, `infra/observability/**`, the new `infra/tests/test_*`,
  ADR 0026, the new READMEs) on the integration branch.
- **Done when:** working tree clean except the known C4 reds; `git status` shows no
  untracked C3 files; the four headline tests run inside the root gate.

## 2. Consolidate the branches

Twelve branches and a multi-author working tree is too many places for the truth to
live. Pick one integration branch, fast-forward/merge C1+C2+C3 onto it, and delete
the stale feature branches. From here on there is one line of work.

- **Done when:** one branch is the canonical head; `git branch` lists only it, `main`,
  and anything genuinely still open.
- **Owner decision:** consolidate straight onto `main` now, or hold on the integration
  branch until step 7 (no `backend/`)? Default if unspecified: hold until step 7.

## 3. Retire the backend dupes that are already safe (C5, wave 1)

> **AMENDED 2026-06-05 — wave-1-now is not executable; folded into the post-C4/C6
> sweep.** A wave-1 attempt found `backend/` is a *monolith*: the held-back top-half
> port sources (`backend/src/{orchestration, frontend, collectors, connectivity}` —
> the very modules C4/C6 port *from*) still import the bottom-half wave-1 targets
> (`config, contracts, storage, surfaces, qc, risk, universe, actor, ...`). You cannot
> "delete the bottom, keep the top" — the tree doesn't separate. The whole of
> `backend/` is already excluded from every gate tool (ruff `exclude`, mypy
> `files=["packages","apps"]`, import-linter root, pytest `testpaths`), so the
> deletions buy **no gate-green progress** and **unblock nothing** (C4/C6 don't depend
> on them). Forcing the split now only yields a dangling-import interim in dead,
> doomed modules and risks tripping C4/C6 if they execute backend code mid-port.
> **Resolution:** there is one C5, run once, *after* C4 and C6 land — at which point
> `frontend` and `collectors/connectivity/orchestration` are also retired, so all of
> `backend/` goes in a single coherent sweep with no half-state. See step 7, which now
> absorbs the whole of C5. The original wave-split below is kept only as the deletion
> *inventory* for that single sweep.

`backend/` is still the whole flat tree. Every module whose canonical copy is already
green can go — but only as one sweep (see the amendment above). Inventory, in
dependency order per [C5-retire-backend.md](C5-retire-backend.md): the M0–M3 stale
dupes (`config, provenance, contracts, storage, fixtures, snapshots, forwards, iv,
surfaces, pricing, risk`), the C2 modules (`qc, validation`), the C1-complete modules
(`actor, universe`), and — once C4/C6 land — `connectivity`, `collectors`, the
collection-coupled orchestration use-cases, `frontend`, and `web`, plus all their
`backend/tests` counterparts.

- **Done when (now part of step 7):** no `backend/` tree; root gate green; lint-imports
  still clean (the `packages → backend` ban catches any dangling ref).

## 4. Consolidate the frontend (C4)

Clear the only red on the gate. Port the real wiring from `backend/src/frontend` into
`apps/frontend` per [C4-frontend.md](C4-frontend.md): repoint the flat imports
(`runner.py`'s `fixtures.library` is the live bug), fix the underlying-selector
default (`AAPL` → `SPX`), clear the 16 mypy errors, and serve real
surfaces/risk/run/config/health/oauth from `packages/infra` instead of fixtures.

- **Done when:** the 1 failing test, 16 mypy errors, and remaining ruff all clear;
  both the root `uv` gate and `npm run lint && npm test` are green; the BFF imports
  only down into `infra`.
- **Owner decision:** the paper-orders / Market router is a Codex extra with no
  `backend` equivalent — port it forward or drop it deliberately? Default if
  unspecified: drop it, recorded in the C4 commit.

## 5. Retire the frontend dupes (C5, wave 2)

With C4 green, delete `backend/src/frontend` and `backend/web` and their tests.

- **Done when:** gone; root gate green.

## 6. Unify the collection seam (C6)

The biggest remaining engineering chunk, and the thing that lets `backend/` fully
retire. Per [C6-collection-seam-unification.md](C6-collection-seam-unification.md)
and [ADR 0027](../../.agent/decisions/0027-collection-seam-push-canonical.md): collapse
the two `BrokerTick` shapes into one (EAV push shape + `sequence`), restore the
content-addressed `event_id` on the **live** capture path, keep `SessionSupervisor`
as the sole reconnect home beneath the adapter, retire the pull seam, port the four
deferred use-cases (`collect_live`, `surface_job`, the handover connectivity-smoke
stage, `provider_flow`) onto the unified collector, and live-wire Saxo/Deribit onto
the Nautilus runtime through it.

- **Done when:** one `BrokerTick`, one collector, one reconnect home; idempotent live
  capture proven against the real store; live==replay on the unified collector; the
  four use-cases green with their `skip`s removed; the pull seam deleted with
  lint-imports still green.

## 7. Retire the rest of backend, make the docs tell the single-tree truth (C5, wave 3)

With C6 landed, the collection-coupled modules are stale. Delete
`backend/src/{connectivity, collectors, orchestration}` and the migrated acceptance
tests, then delete `backend/` entirely (`pyproject.toml`, `uv.lock`, `README.md`, the
empty `src/`). Then make the docs honest: drop the separate `cd backend` gate from
`AGENTS.md`, drop the `Backend` row from `.agent/map.md` and fold its surviving
description into the `Monorepo` row, and fix any README still pointing at
`backend/src/...` as canonical.

- **Done when:** no `backend/` directory; the root gate is the only gate; `git status`
  points nowhere at the flat layout.

## 8. Tree hygiene

The clean-tree gesture. Remove the `Test Lenny/` scratch directory (it is not part of
the canonical structure). Then handle the `Vincent's Code/` reference clone per the
C5 spec: `rm -rf "Vincent's Code"` and drop its three tooling-exclusion lines and the
TASKBOARD note.

- **Owner decision (gates the `Vincent's Code/` removal):** confirm the max-union
  harvest is complete — there is nothing left worth pulling from Vincent's stack.
  Until you confirm, do the `Test Lenny/` removal only and leave the reference clone.

## 9. Minimalism sweep (Q1)

Now the tree is one, make it the *minimal* tree. Across `packages/infra`, delete
everything not load-bearing: the declined Vincent helpers, the YAGNI EventSource seam
(ADR 0016), dead and duplicate paths, vendored-but-unused code. Use the
`review-module-depth` skill per module. "Minimal amount of code needed" should become
an enforced state, not an aspiration.

- **Done when:** every module justifies its existence; nothing dead is importable;
  LOC drops while coverage holds steady.

## 10. Contract-test hardening (Q2)

The explicit "deeply tested at the level of the contracts between modules" mandate.
Map every module-to-module seam — `StorageRepository`, the unified
`MarketDataAdapter`/`BrokerTick`, the actor→analytics `RawMarketEvent` boundary,
`TriageRecord`, the BFF↔infra seam — audit current coverage of each, and write a
dedicated contract suite per seam with independently-derived oracles (see
`tasks/TESTING.md` and the `write-tests` skill). A seam is done when a test fails the
moment *either* side of it drifts.

- **Done when:** each frozen seam has a standalone contract test pinning it; the gate
  enforces it.

## 11. Config and reproducibility (Q3)

The last solidity layer, per
[configuration-and-reproducibility.md](../../documentation/configuration-and-reproducibility.md):
the five fix-tasks — TOML→YAML, the six base YAMLs
(`environment/broker/universe/qc/scenarios/pricing`), generalize the typed
`from_config` + `__post_init__` + `version` pattern, wire config into `infra`, and
stamp the composite config hash into every provenance record. No business parameter
stays a `.py` literal.

- **Owner decision (blocks this step):** OQ-6 — the on-disk profile format (a directory
  of overlays vs a single resolved file, the naming, and how a run references the
  profile it used). See `.agent/open-questions.md`.
- **Done when:** config is YAML → typed → DI'd into the pure compute; the composite
  hash is in every stamp; reproducibility holds across a config change.

---

## Decisions still outstanding (don't block on these to start; they gate specific steps)

- **Scope of "done."** This plan treats *done* as steps 1–11: close the merge, one
  minimal tree, contract-tested, config-clean. The forward index→constituents daily
  analytics pipeline (`documentation/roadmap-index-analytics.md`) is the **next**
  epoch, not this one. Confirm, or say to fold it in.
- **Branch strategy** — gates step 2 (onto `main` now vs hold).
- **Paper-orders router** — gates step 4 (port vs drop).
- **Max-union harvest complete?** — gates the `Vincent's Code/` removal in step 8.
- **OQ-6, profile format** — gates step 11.

## Parallelism (if more than one agent is available)

The list is linear so one agent can follow it start to finish. If you have two: steps
4 (C4, `apps/frontend`) and 6 (C6, `infra/collectors`+`connectivity`+`infra-{saxo,
deribit}`) own disjoint directories and can run at the same time — but each C5 wave
(3, 5, 7) must wait for the port it retires, and steps 9–11 want the single tree from
step 7 in place first. Claim your files on `TASKBOARD.md` before you start, as always.
