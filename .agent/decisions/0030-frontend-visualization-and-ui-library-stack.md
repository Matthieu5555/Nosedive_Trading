# 0030 — Frontend visualization & UI library stack: Plotly.js for charts, shadcn/ui + TanStack Table for UI

- **Status:** accepted, 2026-06-06.
- **Date:** 2026-06-06.
- **Amended:** 2026-06-12 — TradingView Lightweight Charts is now a first-class dependency
  for 2D financial charts (candlesticks and Greek term-structure line charts); Plotly remains
  the 3D/heatmap/non-line analytical chart path.
- **Implements:** applies the **library-leverage** principle of [[0023-nautilus-runtime-spine-and-library-leverage]]
  to the front end; feeds roadmap **1I** (front page) and **Tab 2** UI — the roadmap now lives in
  `TARGET.md` (`documentation/roadmap-index-analytics.md` was removed with the `documentation/` tree).
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

1. **Plotly.js (MIT) remains the analytical charting library for 3D surfaces, heatmaps, and
   non-line views.** It covers `surface`/`mesh3d` for the 3D IV surface, `scatter`/`line` for
   the 2D smile, the stress-scenario heatmap/surface, and future waterfall-style attribution
   views.

2. **shadcn/ui (MIT; Radix + Tailwind, copy-in source) for the UI shell** — layout, tabs, accordion,
   dialog, forms — and **TanStack Table (MIT, headless) for dense data grids.** Both are
   Tailwind-native/headless, carry no paid tier, and coexist cleanly with Plotly's canvas/WebGL
   (no CSS-in-JS specificity wars).

3. **TradingView Lightweight Charts (Apache-2.0) is first-class for 2D financial charts.** It renders
   daily candlesticks and numeric maturity-line charts such as the dollar-Greek term structure, where
   its native crosshair, pan/zoom, and compact canvas footprint are a better fit than Plotly. The app
   uses the built-in attribution logo to satisfy the TradingView link requirement. Lightweight Charts
   is not used for 3D surfaces, heatmaps, or waterfall charts.

## Consequences

- New web dependencies: `plotly.js`, `lightweight-charts`, `shadcn/ui` (+ Radix + Tailwind),
  `@tanstack/react-table`. The front gate (`npm run lint && npm test`) covers them; the root Python
  gate is unchanged.
- *Amendment (2026-06-13):* because these chart libraries render only in a real browser (jsdom has
  no layout engine), an **opt-in Playwright end-to-end suite** (`npm run e2e`, see
  `apps/frontend/README.md`) backstops them with navigation/button-flow and layout-collision /
  overflow checks. It is not part of the front gate, but it is the regression net for visual/layout
  breakage these libraries can introduce — extend it when you add or restyle a chart or panel.
- 1I wires the real pipeline into these components, replacing the deleted mock; dollar Greeks come
  from the risk contract per [[0011-blueprint-as-plan-of-record]] and the $-unit pins (roadmap P0.2).
- Plotly is heavier than a purpose-built financial chart, so daily bars and Greek term-structure
  curves sit on Lightweight Charts. Plotly stays where dimensionality or chart type requires it.

## Alternatives considered (rejected)

- **TradingView Lightweight Charts as the primary chart for everything** — it is the wrong fit for
  the 3D IV surface, stress heatmap/surface, and attribution waterfall. It is used only for the 2D
  financial charts where the interaction model is valuable.
- **ECharts-GL for the 3D surface** — pulls the whole Apache ECharts runtime in for one trace; not
  minimal when Plotly already covers 3D.
- **MUI** — Emotion CSS-in-JS fights Tailwind (specificity/collisions) and the good data grid is
  behind MUI X **Pro/Premium** paid seats — a licence trap for a dense trading grid.
- **Ant Design** — heavy/opinionated visual language, weaker accessibility.
- **Chakra UI** — no first-class data grid and a v3 rewrite that fragmented the ecosystem.
- **AG Grid** — advanced features gated behind an Enterprise licence; TanStack Table is free/headless.
