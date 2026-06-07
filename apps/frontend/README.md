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

## Pages

Seven pages over `react-router`, wrapped in a shared `AppLayout` (top-bar nav):

- **Home** — the index-analytics front page (WS 1I): pick an index, pick a recorded date
  (the "N days recorded" counter + dropdown over completed gap-free runs), scroll the
  point-in-time, price-first constituent list (TanStack Table), then select a ticker to
  see its **price-first** detail — the daily **candlestick** (Plotly), the **3D IV
  surface**, and a **per-maturity accordion** (shadcn/Radix) of the **2D smile** and the
  **dollar Greeks**, each tagged with its P0.2 unit string. Charts are Plotly only
  (ADR 0030); every panel self-labels. Picking a past date re-resolves the basket and
  analytics as-of that date (never today-defaulted).
- **Health** — the operator dashboard: the four flags (data flowing / surfaces
  building / QC passing / scenarios current), the trade date, and the EOD backlog,
  read from `orchestration.build_dashboard` over the store and the run-state ledger.
- **Surfaces** — the fitted SVI slices for an underlying (default `AAPL`, the symbol
  the offline sample chain produces), read back from the `surface_parameters` table.
- **Risk** — net portfolio sensitivities, read back from `risk_aggregates`.
- **Run** — provider listing, pipeline launch, and job polling. The `SAMPLE` provider
  builds a **real** surface by replaying a committed day through the actor pipeline (see
  the live-run path below); the job lifecycle is live.
- **Config** — list and read the platform config files (read-only, traversal-guarded).
- **NotFound** — the catch-all 404.

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
- `GET /api/providers`, `GET /api/run/underlyings`, `POST /api/run`,
  `GET /api/jobs`, `GET /api/jobs/{id}`.
- `GET /api/config`, `GET /api/config/{filename}`.
- `GET /api/price-history[?underlying=&start=&end=]` — daily OHLC bars for one ticker over a
  window, from the `daily_bar` table (WS 1I).
- `GET /api/constituents[?index=&as_of=]` — the point-in-time, price-first index basket via
  the as-of `members` resolver (the no-look-ahead gate), from `index_constituents` (WS 1I).
- `GET /api/analytics[?underlying=&trade_date=]` — the projected tenor × delta-band grid
  (smile + surface slice + dollar Greeks with unit strings) from
  `projected_option_analytics` (WS 1I).
- `GET /api/recorded-dates[?index=]` — the completed, gap-free trade dates + count, from the
  1G run-state ledger (only complete runs, never a raw partition listing) (WS 1I).
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
