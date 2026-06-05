# apps/frontend

The operator frontend: a FastAPI backend-for-frontend (BFF) plus a React/Vite web
app. Top of the layer stack — it reads only *down* into `packages/infra`, never into
`backend`. Owner: **M8**.

## TL;DR

The BFF is the only place infra meets HTTP. Its six routers read the real
`packages/infra` seams — `ParquetStore` for the persisted contract tables, the pure
`surfaces`/`risk` engines, and `orchestration.build_dashboard` — and serialize the
result to JSON-primitive payloads. No business logic lives in the routers; they call
infra and serialize, and surface errors as typed payloads rather than 500s. The web app
is the only consumer above this layer.

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

- **Home** — landing page linking the other views.
- **Health** — the operator dashboard: the four flags (data flowing / surfaces
  building / QC passing / scenarios current), the trade date, and the EOD backlog,
  read from `orchestration.build_dashboard` over the store and the run-state ledger.
- **Surfaces** — the fitted SVI slices for an underlying (default `AAPL`, the symbol
  the offline sample chain produces), read back from the `surface_parameters` table.
- **Risk** — net portfolio sensitivities, read back from `risk_aggregates`.
- **Run** — provider listing, pipeline launch, and job polling. The `SAMPLE` provider's
  surface build is stubbed pending C6 (see below); the job lifecycle is live.
- **Config** — list and read the platform config files (read-only, traversal-guarded).
- **NotFound** — the catch-all 404.

The earlier Codex `Market` / `Risk Scenarios` / `Orders` paper-trading pages and their
`market`/`orders` BFF routers were dropped in C4: they synthesized ~700 lines of fixture
data, had no `backend` equivalent, and are superseded by the store-backed surfaces/risk
routes. No live broker orders were ever sent.

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
- `POST /api/oauth/saxo/start`, `GET /api/oauth/saxo/callback`,
  `GET /api/oauth/saxo/status`, `DELETE /api/oauth/saxo`.

The OAuth flow's verifiable half (single-use CSRF state, authorize-URL construction,
replay/forgery rejection) is real; the token exchange fails closed with a typed `501`
until `packages/infra-saxo` lands.

## Pending C6 — the live-run build path

A surface build starts with a live capture (resolve the chain off a broker session,
collect a window of quotes into the raw layer, then run the actor), so it depends on the
broker-session → `RawMarketEvent` collection seam. That seam
(`orchestration.surface_job` / `collect_live`) is owned by **C6** and has not yet landed
on the `packages` stack — the C3 orchestration package deliberately did *not* port
`build_surface` rather than wire a second, divergent collection path. Until C6 closes the
seam, a `SAMPLE` run settles to `ERROR` with a typed "C6 pending" message; the
queue/poll/state-machine lifecycle around it is fully exercised. See
`runner.py`'s `TODO(C6)` and `tasks/C6-collection-seam-unification.md`.

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
