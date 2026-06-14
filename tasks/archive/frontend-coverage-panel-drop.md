# frontend-coverage-panel-drop — drop <CoveragePanel> into the Market page

> **Front slice of [T-capture-coverage-panel](frontend-capture-coverage-panel.md).** The BFF
> endpoint (`GET /api/coverage`), its serializer, and the `CoverageTable` component **have
> landed** (per the taskboard ready-queue note). The only remaining step is the front
> placement: drop the panel into the Market (Tab 1) page. That last step was deliberately
> left out of the parent so it would not collide with the live front lane editing
> `Market.tsx` / `App.tsx`. This is that step, scoped to the front layer alone.

## Why (TARGET cite)
TARGET §2 goal #1 ("a hella clean frontend; every panel answers what am I looking at") and
the review-priority #1 — "verify tonight's EOD captures are 100% clean and fully populated".
The coverage table is the operator's *see-it-at-a-glance* tool for the tenor-selection
class of capture gaps; until it is mounted on the page, the landed BFF + component are
inert. Index-options-only / SX5E scope (ADR 0042) — the panel reads the index chain.

## Scope boundary
- **In:** mount `<CoverageTable>` (already built) as a collapsible "Capture coverage" panel
  on the Market page, wired to `GET /api/coverage?underlying=<index>&trade_date=<as_of>`
  via the existing `api.ts` typed client. Honest empty/degraded state (reuse the panel's
  own empty render). One component test asserting it appears on the page with a populated
  payload and shows the empty state with `n_expiries=0`.
- **Out:** the BFF endpoint, serializer, `CoverageTable` component, and tenor-map logic —
  all landed in the parent. No recompute, no new BFF route. Quote-completeness from
  `raw_market_events` is the parent's explicit phase-2 add, not here.

## Coordination (shared tree)
The placement edit touches `apps/frontend/web/src/pages/Market.tsx` (and possibly
`App.tsx`) — the live front lane. Serialize against `frontend-page1-cdc-buildout` (which
also reflows `Market.tsx`): land the CDC reflow first, then drop this panel into the
reflowed order, or coordinate the single edit. Do not cross the same file in parallel.

## Done criteria
The `CoverageTable` renders as a labelled, collapsible panel on Tab 1, fed by
`/api/coverage` for the selected `(index, trade_date)`; the captured-2026-06-11 gap is
visible at a glance; component test green; web gate green (`npm run lint && npm test`).
