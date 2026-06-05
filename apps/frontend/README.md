# apps/frontend

FastAPI BFF + React/Vite operator frontend. Owner: **M8**.

## TL;DR

This is the contract-first frontend layer. The BFF exposes stable JSON routes
backed by deterministic fixtures until the merged infra packages for storage,
surfaces, risk, orchestration, and broker execution are ready to wire in.

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

The Vite dev server proxies `/api` and `/healthz` to `127.0.0.1:8000`.

## Pages

- `Market` defaults to `SPX` and displays the selected index snapshot, component
  stock snapshots, option bid/ask chain, aggregate and line greeks, and the
  volatility surface.
- `Risk Scenarios` posts deterministic paper scenarios and displays PnL,
  before/after greeks, a spot risk ladder (PnL plus delta/gamma/vega/theta
  curves over -10%..+10% spot shocks, with the requested shock marked),
  greeks-by-expiry bar charts (vega/theta/gamma), and a spot x vol grid.
  Charts are hand-rolled SVG components (`LineChart`, `BarChart`) — no
  charting dependency.
- `Orders` provides paper order preview, paper submission, open orders, and
  history. It does not send live broker orders.

## API

- `GET /healthz`
- `GET /api/underlyings`
- `GET /api/market?underlying=SPX`
- `GET /api/risk/scenarios?underlying=SPX`
- `POST /api/risk/scenarios`

`ScenarioResult` carries, besides the headline PnL and before/after greeks:
`ladder` (PnL and shocked greeks per spot rung, sharing the same model as
`greek_after`, so the rung at the requested shock matches it exactly) and
`expiry_buckets` (per-expiry aggregate greeks summing to the chain totals).
- `GET /api/orders`
- `POST /api/orders/preview`
- `POST /api/orders`

## Verify

Python API tests:

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

The repo-wide Python gate remains:

```
uv run ruff check . && uv run mypy . && uv run import-linter && uv run pytest -q
```
