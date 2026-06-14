# apps/frontend

The operator frontend: a FastAPI backend-for-frontend (BFF) plus a React/Vite web
app. Top of the layer stack — it reads only *down* into `packages/infra`, never up into
`strategy`/`execution` (import-linter enforces this). Owner: **M8**.

## TL;DR

The BFF is the only place infra meets HTTP. Its routers read the real
`packages/infra` seams — `ParquetStore` for the persisted contract tables, the pure
`surfaces`/`risk` engines, the as-of `universe.members` resolver, the `run_state` ledger,
and `orchestration.build_dashboard` — and serialize the result to JSON-primitive payloads.
No business logic lives in the routers; they call infra and serialize, and surface errors
as typed payloads rather than 500s. The store opens read-only — only the EOD cron writes
(ADR 0034 §1). The web app is the only consumer above this layer.

Run the BFF:

```
uv run uvicorn algotrading.frontend.app:app --reload --host 127.0.0.1 --port 8000
```

Run the web app:

```
cd apps/frontend/web
npm install
npm run dev
```

The Vite dev server proxies `/api` and `/healthz` to `127.0.0.1:8000`, so the web app
and API share an origin in development (no CORS dance); `FRONTEND_BASE_URL` covers
production CORS.

## Tests

Two layers, both under `apps/frontend/web`:

- **Component tests** (`npm test`, Vitest + Testing Library + MSW, jsdom) — the verification
  gate, alongside `npm run lint`. They cover render, data fetching and handlers per component.
- **End-to-end tests** (`npm run e2e`, Playwright, real Chromium) — what jsdom cannot do:
  navigation/button flows across routes and **layout-collision / overflow** checks (elements
  don't overlap, controls stay on-screen, no horizontal overflow) at desktop, laptop and narrow
  viewports. Specs live in `web/e2e/`; the BFF is mocked at the network layer with the same
  contract fixtures the component tests use, so the suite is deterministic and never touches a
  live BFF or the canonical data store. Playwright boots the Vite dev server itself.

  E2E is **opt-in**, not part of `npm test`: it needs a browser binary
  (`npx playwright install chromium`, one-time ~110 MB) and a running dev server, so wiring it
  into the shared gate is a team decision. Run it locally with `npm run e2e`
  (`npm run e2e:ui` for the inspector, `npm run e2e:report` for the last HTML report).

## Pages

Three operator pages over `react-router`, wrapped in the shared top-bar shell:

- **Home** — the index-analytics front page (WS 1I): pick an index, pick a recorded date
  (the "N days recorded" counter + dropdown over completed gap-free runs), scroll the
  point-in-time, price-first constituent list (TanStack Table), preload the daily OHLC history for
  **every constituent** through `/api/price-history/batch`, then select a ticker to see its
  **price-first** detail — the daily **candlestick** (TradingView Lightweight Charts), the **3D IV
  surface**, and a **per-maturity accordion** (shadcn/Radix) of the **2D smile** and the
  **dollar Greeks**, each tagged with its P0.2 unit string. TradingView Lightweight Charts
  renders the daily candlesticks and dollar-Greek term-structure line charts; Plotly remains
  the 3D/heatmap/non-line chart path (ADR 0030). Every panel self-labels. Picking a past date
  re-resolves the basket and analytics as-of that date (never today-defaulted).
- **Risk Scenarios** — the full-reprice stress surface over spot and vol shocks, read from
  `/api/risk/scenarios`, with the portfolio selector from `/api/risk/portfolios`. The same
  surface rendering (the shared `StressSurface` component) also backs the Basket Builder's
  on-demand **Stress basket** action (`POST /api/basket/scenarios`), so a composed basket can be
  stressed live without a persisted portfolio.
- **Orders** — the read-only Phase-3 execution sketch. The ticket is browser-local and submit is
  disabled until the explicit order-gate work lands.

The earlier Codex `Market` / `Risk Scenarios` / `Orders` paper-trading pages and their
`market`/`orders` BFF routers were dropped in C4: they synthesized ~700 lines of fixture
data with no equivalent in the canonical stack, and are superseded by the store-backed
surfaces/risk routes. No live broker orders were ever sent.

## API

The BFF exposes (all under `/api` except the liveness probe):

- `GET /healthz` — liveness (no infra reads).
- `GET /api/health[?trade_date=YYYY-MM-DD]` — operator dashboard status.
- `GET /api/surfaces[?underlying=&trade_date=]`, `GET /api/surfaces/underlyings`.
- `GET /api/risk[?portfolio_id=]`, `GET /api/risk/portfolios`,
  `GET /api/risk/scenarios[?portfolio_id=]`.
- `POST /api/basket/risk` — price/risk a composed multi-leg basket as the book-additive sum of
  its legs' stored dollar Greeks (WS 2A; summation, never a reprice).
- `POST /api/basket/scenarios` — the **on-demand** full-reprice stress surface for a composed
  basket (WS 2B): reconstructs a valuation per option leg from the stored grid and reprices over
  the config-driven (spot × vol) grid, returning the same `surface` shape as
  `/api/risk/scenarios` plus the worst-case cell and labelled per-leg gaps. The interactive,
  no-cron counterpart to the persisted-surface read — works off today's analytics without a
  configured portfolio.
- `GET /api/providers`, `GET /api/run/underlyings`, `POST /api/run`,
  `GET /api/jobs`, `GET /api/jobs/{id}`.
- `GET /api/config`, `GET /api/config/{filename}`.
- `GET /api/price-history[?underlying=&start=&end=]` — daily OHLC bars for one ticker over a
  window, from the `daily_bar` table (WS 1I).
- `GET|POST /api/price-history/batch` — grouped daily OHLC histories for a requested list of
  underlyings. The front uses `POST` with `underlyings[]` and `end=<as_of>` so the first page has
  all constituent histories without one browser request per ticker.
- `GET /api/constituents[?index=&as_of=]` — the point-in-time index basket via the as-of
  `members` resolver (the no-look-ahead gate), from `index_constituents`; the web app orders it
  by **index weight** (market-cap proxy) and default-selects the heaviest name (WS 1I).
- `GET /api/analytics[?underlying=&trade_date=]` — the projected tenor × delta-band grid
  (smile + surface slice + dollar Greeks with unit strings) from `projected_option_analytics`
  (WS 1I). **Index-keyed:** the option chain is captured at the index level, so the web app
  queries this with the *index* symbol (the vol surface is the index's), not the selected
  constituent — the constituent selection only drives its price candlestick. The smile's
  x-axis declares itself via `axis_type` (F-BFF-04): `"delta"` + `deltas` on the rich
  projection, `"moneyness"` + `moneyness_buckets` on the surface-grid fallback — bucket
  values are never relabelled as deltas. Each `surface_slice` carries the full fit
  diagnostics (`bound_hits`/`converged` beside `rmse`/`n_points`/`arb_free`) plus the
  derived `degenerate`/`degenerate_reasons` flag, so a railed SVI calibration renders
  flagged, never as clean. The stress surface (`GET /api/risk/scenarios`) labels missing
  `(spot, vol)` cells as `null` holes with `has_holes`/`n_holes` (F-BFF-03), never `0.0`.
- `GET /api/recorded-dates[?index=]` — from the 1G run-state ledger. Returns `dates`/`count`
  (the **qc-clean, gap-free** days — the operator coverage figure) **and** `available`: every
  **viewable** day (whose `analytics` stage produced a surface, **including qc-failing ones**),
  each tagged with its QC verdict (`pass`/`fail`/`unknown`). The date picker offers `available`
  and shows a QC badge, so a degraded snapshot is shown rather than hidden (WS 1I).
- `GET /api/attribution[?trade_date=&portfolio_id=&level=&contract_key=]` — the by-Greek P&L
  decomposition for one persisted `scenario_attributions` record (TARGET §2 #5 / §7 #2). Projects
  the frozen `ScenarioAttribution` seam **verbatim** (the BFF re-decomposes nothing): `terms` are
  the per-Greek dollar contributions in the ADR-0030 dPnL order (Δ → Γ → Vega → Θ; Rho/Vanna/Volga
  appended by the second-order-greeks lane as the seam grows), each a labelled `{name, dollars,
  unit}`; `residual` is the honesty meter against the full reprice carried as its **own** bar
  (never folded into a term); `verdict` is the engine's `within_tolerance` ruling against its
  echoed `residual_abs_tol`/`residual_rel_tol`. `level=book` (default, the book sentinel
  `contract_key`) or `level=position` + a `contract_key` for the §5.8 per-position drill. No
  record for the `(portfolio, date)` is a labelled-empty `found=false` body (HTTP 200), a bad
  `trade_date` a labelled `400`. The web `AttributionWaterfall` (Plotly waterfall) renders it on
  the Basket page beside the stress surface.
- `POST /api/oauth/saxo/start`, `GET /api/oauth/saxo/callback`,
  `GET /api/oauth/saxo/status`, `DELETE /api/oauth/saxo`.

The OAuth flow's verifiable half (single-use CSRF state, authorize-URL construction,
replay/forgery rejection) is real; the token exchange fails closed with a typed `501`
until `packages/infra-saxo` lands.

## The live-run build path (SAMPLE)

A surface build runs the unified collection seam (`orchestration.build_surface` over
`collect_live`, ADR 0027) end to end. The `SAMPLE` provider drives it deterministically:
`runner.py` reads the store's most recent committed day, replays it through the **exact**
actor pipeline into a **throwaway temp store** (`persist=False`, so a SAMPLE run never
writes back into `data/` — re-capturing the same content-addressed events would be a
no-op append anyway), and reduces the fitted surface to a small job summary the web app
polls. The queue/poll/state-machine job lifecycle wraps it; any failure marks the job
`ERROR` and is logged. A run needs a committed day to replay — a `SAMPLE` against an
empty store fails fast with a typed error.

Live broker providers (Saxo/Deribit/IBKR) capture through the same `build_surface` seam;
the broker-session → `RawMarketEvent` normalization lives in the
`packages/infra-{saxo,deribit,ibkr}` adapters. See `runner.py` and `infra/orchestration`.

## Verify

Python API tests (run under the root gate; `pythonpath`/testpaths are wired in the root
`pyproject.toml`):

```
uv run pytest apps/frontend/tests -q
```

Web gate:

```
cd apps/frontend/web
npm run lint
npm test
npm run build
```

The repo-wide Python gate:

```
uv run ruff check . && uv run mypy . && uv run lint-imports && uv run pytest -q
```
