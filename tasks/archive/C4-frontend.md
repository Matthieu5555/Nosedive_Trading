# C4 — Consolidate the two frontends into `apps/frontend`

- **Owns:** `apps/frontend/**`; **deletes** `backend/src/frontend/**` and `backend/web/**`.
- **Depends on:** C1/C2/C3 seams landed in `packages` (the BFF reads `ParquetStore`, surfaces/risk, `build_dashboard`, `build_surface`). Can start the port early against stubs; final wiring needs the seams.
- **Blocks:** C5 (retiring `backend/{src/frontend,web}`).
- **State going in:** **two competing copies.** `apps/frontend` (the correct architectural home — top layer above `packages/infra`) is a **fixtures-only shell**: three routers served from ~700 lines of hand-synthesized data, imports nothing from `packages/infra`. `backend/src/frontend` (in the doomed flat tree, under bare non-namespaced imports) has the **real wiring**: six routers reading the genuine seams, including a real run→persist→read-back path and the OAuth state lifecycle. Right work, wrong place.

## Objective

One frontend, in the canonical home `apps/frontend`, with the real infra wiring — not the fixture shell.

## What to do

1. **Port the real wiring** from `backend/src/frontend` into `apps/frontend/src/algotrading/frontend`, re-pointed to the merged namespace:
   - the six routers (`health`, `surfaces`, `risk`, `run`, `config`, `oauth`) plus `context.py`, `serializers.py`, `runner.py`, `providers.py`, `oauth_state.py`;
   - rewrite every flat import to the merged seam (`storage` → `algotrading.infra.storage`, `orchestration` → `algotrading.infra.orchestration`, etc.) — this is the main task, and import-linter will enforce that the BFF only imports **down** into `packages/infra`, never into `backend`;
   - confirm `build_surface` / `ParquetStore` / `build_dashboard` signatures match the post-C1/C2/C3 seams (the wiring was written against flat `backend/src`).
2. **Decide on Codex's extras** so nothing useful is lost: `apps/frontend`'s `orders`/paper-trading router and Market page have no `backend` equivalent. If paper orders are in scope, port them forward; otherwise drop them deliberately.
3. **Port the web app:** the 7 pages (Home/Health/Surfaces/Risk/Run/Config/NotFound) with `react-router` + `AppLayout`, plus the test helpers.
4. **Wire both gates:** the Python BFF under the root `uv` gate (import-linter contract for `algotrading.frontend` passes); the web app under `npm run lint && npm test`.

## Frozen seam

The BFF reads only infra seams (down-layer) — `ParquetStore` reads, surfaces/risk contracts, `build_dashboard`/`build_surface`. It never reaches into `backend`. OAuth/live-broker network paths fail closed where `infra-saxo` is absent.

## Test surface

- **API contract tests** over every router, with independently-derived oracles, including the **real run → persist → read-back path** and OAuth single-use-state replay rejection.
- **Web component tests** asserting loading / empty / error / happy states with a shared fetch mock.

## Done criteria

One frontend under `apps/frontend`, wired to the real `packages/infra` seams; both gates green; `backend/src/frontend` and `backend/web` deleted (hand to C5 if you don't delete them here).

## Gotchas

`apps/frontend` is the home; do not "fix" the BFF by keeping it in `backend`. The fixture shell is a placeholder — the done bar is serving real surfaces/risk/run/config/health/oauth, not synthesized data.
