# REP10 — Front-end information architecture: the two-tab shell before Phase 2

> **READY — governance + light code, do before Phase 2 routes land.**
> Dashboard-review follow-up (medium-term vision + course transcript;
> [AUDIT-tasks-coherence-2026-06-07.md](AUDIT-tasks-coherence-2026-06-07.md)).
> The vision is a **two-tab** product (Tab 1 — index data; Tab 2 — risk & strategy) with an
> execution **sketch**. The router today mixes the operator front page with five dev/ops pages
> as flat siblings, and Phase 2 (2A–2D) is about to add task pages one route at a time with no
> overarching structure.

- **Owns:** web `apps/frontend/web/src/` — `App.tsx` (the route table),
  `components/AppLayout.tsx` (the nav shell), and the nav grouping; optionally a one-clause
  note on [ADR 0030](../.agent/decisions/0030-frontend-visualization-and-ui-library-stack.md)
  recording the IA. Does **not** rebuild any page's content.
- **Depends on:** nothing. Pairs with [REP3](REP3-frontend-tanstack.md) and
  [REP4](REP4-shadcn-decision.md) (shell/primitive work) — do alongside if convenient.
- **Blocks:** nothing hard, but Phase 2 specs (`2A`–`2D`) say "register the page/route" with no
  agreed home — land the IA **first** so they slot into Tab 2 instead of growing the flat list.
- **State going in:** `App.tsx` routes `/` (HomePage = Tab 1) plus flat `health`, `surfaces`,
  `risk`, `run`, `config` — the latter five are **dev/ops/legacy** read-outs (real BFF data),
  presented as peers of the operator front page. There is no "Tab 1 / Tab 2 / exec" grouping;
  `pages/Risk.tsx` and `pages/Surfaces.tsx` are engine read-outs, **not** the prof's Tab-2
  risk-&-strategy pages (those are 2A basket builder, 2B stress, 2C attribution, 2D composition,
  per the [roadmap](../documentation/roadmap-index-analytics.md) and `vision-medium-term.md`).

## Objective

A nav shell that reflects the product: **Tab 1 (index data / operator)**, **Tab 2 (risk &
strategy)**, and a clearly-secondary place for the execution sketch and the dev/ops read-outs —
so Phase 2 pages have a defined home and the operator is not handed a flat list of engineering
panels.

## What to do (ordered)

1. **Decide the IA (owner ruling), record it.** The proposed structure:
   - **Tab 1 — Index data:** the operator front page (`HomePage`, WS 1I) — pick index → date →
     constituent → price-first detail (candlestick, fitted surface, smile, decimal+$ Greeks).
   - **Tab 2 — Risk & strategy:** the Phase-2 pages — basket builder (2A), stress surface (2B),
     PnL attribution (2C), strategy composition (2D). Empty/"coming soon" placeholders until
     each lands, so the tab exists before its pages do.
   - **Exec (sketch):** the order ticket (3A) / sign-and-send (3B) — explicitly secondary, the
     prof's *"partie la plus étrange"*.
   - **Ops / debug:** the existing `surfaces`/`risk`/`run`/`config`/`health` engine read-outs,
     grouped and demoted (a separate nav section or a `/ops/*` prefix), not peers of Tab 1.
   Record the chosen structure (a clause in ADR 0030 or a short note linked from it).
2. **Restructure `App.tsx` + `AppLayout.tsx` to the chosen IA** — nested routes / route groups
   (`/`, `/risk-strategy/*`, `/exec/*`, `/ops/*`) and a top-level tab nav that names Tab 1 /
   Tab 2 / Exec, with ops behind a secondary affordance. Keep every existing page reachable and
   every existing test green — this is a **navigation/grouping** change, not a page rewrite.
3. **Reserve the Tab-2 / Exec slots** with labelled placeholders so 2A–2D / 3A–3B register into
   the agreed routes rather than re-deciding the IA each.
4. **Update the Phase-2/3 specs' "register the page/route" steps** (or leave a pointer here) so
   they target the Tab-2 / Exec groups defined here.

## Done when

The nav presents Tab 1 / Tab 2 / Exec with the dev/ops pages grouped and demoted; the IA is
recorded (ADR 0030 clause or linked note); Phase-2/3 pages have a defined route home;
`npm run lint && npm test` green with every existing page still reachable.

## Gotchas

- **Grouping, not rebuilding.** Do not touch page content (REP3 owns the chart/Greek panels);
  this task only moves routes and shapes the nav.
- **Tab 2 ≠ the legacy `risk`/`surfaces` pages.** Those are engine read-outs for ops; the
  prof's Tab 2 is the basket/stress/attribution/composition product (2A–2D). Don't conflate
  them in the nav.
- **The exec area stays visibly secondary** — it is a sketch (ADR 0037 defers futures; 3A/3B
  are transmission-disabled by default); the IA should not present it as a co-equal product tab.
