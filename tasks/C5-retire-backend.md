# C5 — Retire the `backend/` flat tree

- **Owns:** deletion of `backend/**` module-by-module, plus the doc updates that follow (`.agent/map.md`, the per-directory READMEs, the `AGENTS.md` gate section).
- **Depends on:** each module's retirement waits on its canonical copy landing green in `packages/`/`apps/`. M0–M3 are ready now; the rest follow C1–C4.
- **Blocks:** nothing — this is the close. When it finishes there is one tree.
- **State going in:** `backend/` is the pre-merge flat layout. M0–M3's modules there are **byte-identical stale dupes** of the `packages/infra` canonical copies (verified). The rest retire as C1–C4 land them.

## Objective

A clean working tree: `packages/`, `apps/`, `documentation/`, `notebooks/`, `.agent/`, `tasks/` — **no `backend/`**, one gate.

## What to do

Retire in dependency order. A module leaves only once its canonical copy is green in the root gate and nothing imports the old path (import-linter already forbids `packages` → `backend`).

1. **Now (M0–M3 — stale dupes):** delete `backend/src/{config,provenance,contracts,storage,fixtures,snapshots,forwards,iv,surfaces,pricing,risk}` and their `backend/tests/test_*` counterparts.
2. **After C1:** delete `backend/src/{actor,connectivity,collectors,universe}` + tests.
3. **After C2:** delete `backend/src/{qc,validation}` + tests.
4. **After C3:** delete `backend/src/orchestration` + the migrated `backend/tests` acceptance tests.
5. **After C4:** delete `backend/src/frontend` + `backend/web`.
6. **Last:** delete `backend/` entirely (`pyproject.toml`, `uv.lock`, `README.md`, the now-empty `src/`).

Then make the docs tell the truth:
- **`AGENTS.md` "Verify before you declare done":** drop the separate `cd backend && ...` gate; the root `uv run ruff check . && uv run mypy . && uv run lint-imports && uv run pytest -q` is now the only gate. Remove the "pre-restructure flat build still lives under `backend/`" paragraph and the "root gate deliberately excludes `backend/`" note.
- **`.agent/map.md`:** drop the `Backend` row; fold its surviving description into the `Monorepo` row (which is now the whole system, not a "merge target").
- **Per-directory READMEs:** any README that still points at `backend/src/...` as canonical is corrected to the `packages/infra/...` home.
- **`frontend/` stub README** (`.agent/map.md` row 16): reconcile with the real `apps/frontend` home from C4.

## Frozen seam

None added — this is removal plus doc truth.

## Test surface

After each deletion the **root gate stays green** and no test references the removed tree. The import-linter guard (`packages` must not import `backend`) already protects against a dangling reference; a deletion that breaks it is caught immediately.

## Done criteria

No `backend/` directory; the root gate is the only gate; `AGENTS.md`, `.agent/map.md`, and the READMEs describe a single tree. `git status` shows nothing pointing back at the flat layout.

## Gotchas

Delete a module only **after** its replacement is gated green — never both-gone-at-once. The order matters: the acceptance tests (C3) are the proof the actor and analytics still work post-port; don't delete the old tree's tests until the new ones run green in the root gate.
