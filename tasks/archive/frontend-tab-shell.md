# frontend-tab-shell — the four-tab scaffold for the web shell

Status: **landed 2026-06-16**, gate green (web lint + vitest + e2e, minus a pre-existing
unrelated Market e2e failure — see Gate below).

## What this is

The shell scaffold the rest of the frontend fleet fills in. It adds four new top-level
tabs to `apps/frontend/web` — **Operations**, **Signals**, **Strategy**, **Positions** —
alongside the existing Market / Basket / Risk Scenarios, for **seven** tabs total. Each new
tab is an **intentional empty-state stub**: a page header plus a plain "No data yet" panel
behind the existing `Guarded`/`ErrorBoundary` wrapper. No real data is wired.

## The contract the fleet keys off

After this lands, every later frontend agent edits **only its own page file** and **never
touches `routes.ts` or `App.tsx` again**. The page filenames are fixed — build against these
exact names:

| Tab | Route | Page file | Component |
|-----|-------|-----------|-----------|
| Operations | `/operations` | `src/pages/Operations.tsx` | `OperationsPage` |
| Signals | `/signals` | `src/pages/Signals.tsx` | `SignalsPage` |
| Strategy | `/strategy` | `src/pages/Strategy.tsx` | `StrategyPage` |
| Positions | `/positions` | `src/pages/Positions.tsx` | `PositionsPage` |

A tab is registered in exactly two places, both following the existing pattern verbatim:
`ROUTES` in `src/routes.ts` (the single nav/route table) and the `PAGES` map + page import
in `src/App.tsx`. The `<nav>` and the `<Routes>` both render from `ROUTES`, so adding a row
there is all a tab needs; the layout e2e already iterates `ROUTES` and so covers new tabs.

## Files changed

- `apps/frontend/web/src/routes.ts` — four new `ROUTES` rows.
- `apps/frontend/web/src/App.tsx` — four page imports + four `PAGES` entries.
- `apps/frontend/web/src/pages/{Operations,Signals,Strategy,Positions}.tsx` — new stub pages.
- `apps/frontend/web/src/index.css` — a `981–1180px` topbar band that tightens nav-button
  sizing so seven tabs fit on one row at laptop width without horizontal overflow (the
  3-tab nav fit at 1024px; 7 did not). Below 980px the nav already becomes a horizontal
  scroller by the existing design; above 1180px the full-size grid has room.
- `apps/frontend/web/src/App.test.tsx` — vitest routing coverage for the four new tabs
  (reach by nav click + empty-state, direct-addressability + active link).
- `apps/frontend/web/e2e/navigation.spec.ts` — all seven tabs in `TABS` (present/active +
  per-tab routing) and a stub-specific empty-state assertion for the four new tabs.
- `apps/frontend/README.md` — route list updated to seven tabs.

## Gate (measured 2026-06-16)

- `npm run lint` — clean (exit 0).
- `npm test` — 27 files, 157 tests passed.
- `npm run e2e` — 39 passed, 1 failed. The single failure
  (`pages.spec.ts:9` "Market: index and as-of selectors") is **pre-existing**: it fails
  identically on the pristine base commit `b5b4ea2` with this slice's files untouched, so it
  is not introduced here. Every new-tab navigation, layout-collision/overflow (3 viewports ×
  7 routes), and empty-state-stub test is green.
