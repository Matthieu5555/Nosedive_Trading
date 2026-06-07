# 0030 — Frontend visualization & UI library stack: Plotly.js for charts, shadcn/ui + TanStack Table for UI

- **Status:** accepted, 2026-06-06.
- **Date:** 2026-06-06.
- **Implements:** applies the **library-leverage** principle of [[0023-nautilus-runtime-spine-and-library-leverage]]
  to the front end; feeds roadmap **1I** (front page) and **Tab 2** UI
  ([`documentation/roadmap-index-analytics.md`](../../documentation/roadmap-index-analytics.md)).
- **Relates to:** [[0011-blueprint-as-plan-of-record]] (blueprint governs domain, not UI), the C4
  consolidation that deleted the fixture-only `/api/market` router.

## Context

The operator front page (1I) must show, per ticker: a **candlestick** price-history chart over years
of daily OHLC bars, an interactive **3D implied-vol surface** (replacing an unreadable 2D view), a
**2D smile** (vol vs delta) per maturity, an accordion per maturity, and **dollar Greeks** — laid out
price-first on a data-dense page. The current front is `apps/frontend` (Python BFF) + a React + Vite +
TypeScript SPA; the old `/api/market` dashboard was 100% fixtures and was removed in C4, so 1I is a
genuine build, not a tweak.

A web-sourced "buy vs build" audit (deep-research, 2026-06-06) evaluated charting libraries and React
UI kits against our constraints. The owner's rule holds throughout: **a dependency only if it is
genuinely justified, and the fewest that cover the need.**

## Decision

1. **Plotly.js (MIT) is the single charting library.** It covers all three needs from one dependency:
   `candlestick`/`ohlc` traces for price history, `scatter`/`line` for the 2D smile, and
   `surface`/`mesh3d` for the 3D IV surface. No second charting dependency.

2. **shadcn/ui (MIT; Radix + Tailwind, copy-in source) for the UI shell** — layout, tabs, accordion,
   dialog, forms — and **TanStack Table (MIT, headless) for dense data grids.** Both are
   Tailwind-native/headless, carry no paid tier, and coexist cleanly with Plotly's canvas/WebGL
   (no CSS-in-JS specificity wars).

3. **TradingView Lightweight Charts (Apache-2.0) is kept as a documented nice-to-have fallback for the
   price chart only.** Adopt it *iff* Plotly's candlestick proves too heavy, ugly, or janky for daily
   bars in practice. It is **not** a dependency now; if adopted it carries a NOTICE-file attribution
   obligation (credit TradingView + link).

## Consequences

- New web dependencies: `plotly.js`, `shadcn/ui` (+ Radix + Tailwind), `@tanstack/react-table`. The
  front gate (`npm run lint && npm test`) covers them; the root Python gate is unchanged.
- 1I wires the real pipeline into these components, replacing the deleted mock; dollar Greeks come
  from the risk contract per [[0011-blueprint-as-plan-of-record]] and the $-unit pins (roadmap P0.2).
- Plotly is heavier than a purpose-built financial chart, but for daily bars on a single-operator
  dashboard the volume is trivial; revisit only via the fallback in Decision 3.

## Alternatives considered (rejected)

- **TradingView Lightweight Charts as the primary chart** — a second charting dependency unjustified
  for daily bars, plus an attribution obligation. Demoted to fallback (Decision 3).
- **ECharts-GL for the 3D surface** — pulls the whole Apache ECharts runtime in for one trace; not
  minimal when Plotly already covers 3D.
- **MUI** — Emotion CSS-in-JS fights Tailwind (specificity/collisions) and the good data grid is
  behind MUI X **Pro/Premium** paid seats — a licence trap for a dense trading grid.
- **Ant Design** — heavy/opinionated visual language, weaker accessibility.
- **Chakra UI** — no first-class data grid and a v3 rewrite that fragmented the ecosystem.
- **AG Grid** — advanced features gated behind an Enterprise licence; TanStack Table is free/headless.
