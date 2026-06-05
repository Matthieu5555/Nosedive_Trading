# frontend — FastAPI backend-for-frontend (BFF)

The operator-facing HTTP layer over the flat backend (Workstream **M8**). It is the only
place where infra meets HTTP: routers read our typed contracts back through
`storage.ParquetStore`, fit/aggregate through the pure-function `surfaces`/`risk` engines,
drive a run through `orchestration.build_surface`, and report health through
`orchestration.build_dashboard` — then serialize the result to JSON-primitive payloads.
No business logic lives in the routers; they call infra and serialize, and surface errors
as typed payloads, never 500s.

> **Layout note.** Built in the flat `backend/` layout per the taskboard (M0 relocates it
> to `apps/frontend` later, as with M6/M7). It is self-contained under
> `backend/src/frontend/**` + `backend/web/**` so the relocation is a move, not a rewrite.

## TL;DR — run it

```bash
# API (serves on http://127.0.0.1:8000)
cd backend && PYTHONPATH=src uv run python -m frontend

# Web app (dev server on http://localhost:5173, proxies /api to the BFF)
cd backend/web && npm install && npm run dev
```

Then open the web app, go to **Run**, launch the `SAMPLE` provider, and watch the
**Surfaces** / **Health** pages fill in.

## Endpoints

| Route | What it does | Infra seam |
|-------|--------------|------------|
| `GET /healthz` | Liveness (no infra read) | — |
| `GET /api/health` | Operator dashboard: the four health flags for a trade date | `orchestration.build_dashboard` |
| `GET /api/surfaces?underlying=&trade_date=` | Fitted SVI slices for an underlying | `ParquetStore.read("surface_parameters")` |
| `GET /api/surfaces/underlyings` | Underlyings with a persisted surface | `ParquetStore.list_partitions` |
| `GET /api/risk?portfolio_id=` | Net portfolio sensitivities | `ParquetStore.read("risk_aggregates")` |
| `GET /api/risk/scenarios?portfolio_id=` | Stress-scenario PnL cells | `ParquetStore.read("scenario_results")` |
| `GET /api/providers` | Provider selector capabilities | `frontend.providers` |
| `POST /api/run` | Launch a run job (`{provider, underlying?}`) | `frontend.runner` → `orchestration.build_surface` |
| `GET /api/jobs[/{id}]` | Poll run job status | in-memory job store |
| `GET /api/config[/{name}]` | List / read the platform config files | `configs/` on disk |
| `POST/GET/DELETE /api/oauth/saxo/*` | Saxo OAuth web flow (CSRF half) | `frontend.oauth_state` |

## The SAMPLE run

`POST /api/run {"provider": "SAMPLE"}` drives `orchestration.build_surface` over the
committed `synthetic_known_answer` chain fixture through the **exact** actor pipeline a
live run uses (`snapshots → forwards → IV → SVI surface`), and persists the fitted surface
into the context's store — so the surfaces/health endpoints then read real data back. No
network is required, which is why this is the path the gate exercises end to end.

## Configuration

- `FRONTEND_BASE_URL` — allowed CORS origin (default `http://localhost:5173`).
- `SAXO_AUTHORIZE_URL` / `SAXO_CLIENT_ID` / `SAXO_REDIRECT_URI` — Saxo OAuth params. Read
  from the environment so no secret is hard-coded or shipped to the browser.
- The store root and configs dir are resolved from the repo root by `AppContext.build()`;
  both are injectable for tests (`store_root=`, see `context.py`).

## Caveats (honest scope)

- **Live brokers are not wired here.** The flat backend has no Saxo/Deribit flows and no
  OAuth token exchange — those arrive with `packages/infra-saxo` / `-deribit` under the
  restructure (M4/M5). `IBKR` is declared but reported `unavailable` (needs `ib_async` + a
  gateway); Saxo OAuth validates CSRF state and constructs the authorize URL, then fails
  closed with a typed `saxo_backend_not_configured` (501). Everything verifiable offline is
  real; the rest is honestly marked, not faked.
- `build_dashboard`'s `events_total` reflects the metrics registry, which is fresh per
  request here (no live collector), so it reads `0`; the health **flags** still reflect
  what is actually on disk (partition presence), which is the operator-relevant signal.

## Tests

`backend/tests/test_frontend_api.py` exercises every router over a tmp-store `AppContext`
via FastAPI's `TestClient`, including a real offline `SAMPLE` run read back through the
API. Web component tests live under `backend/web/src/pages/*.test.tsx`.
