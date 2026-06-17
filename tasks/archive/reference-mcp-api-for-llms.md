# reference-mcp-api-for-llms — expose the engine to LLMs via MCP over the BFF API

**Owner:** Matthieu · **Lane:** `platform-`/`reference-` · **Priority:** PARKED — explicitly
**not a priority** (owner, 2026-06-15). Captured so it is not forgotten; do not start without a
fresh owner go.

## Idea

Wrap the existing BFF API (`apps/frontend` — `/api/indices`, `/api/analytics`, `/api/surfaces`,
`/api/constituents`, `/api/risk`, the basket/ticket/booking endpoints) as **MCP server(s)** so an
LLM agent can drive the analytics engine directly (query a surface, price a basket, run a scenario,
preview a ticket) instead of via bespoke glue. The API is already the clean down-layer seam (BFF
reads only `packages/infra` seams), so an MCP facade is a thin, read-mostly adapter — the
write/booking side stays behind the existing password gate.

## When revisited — scope sketch

- Read tools first (surfaces, analytics, coverage, constituents, scenarios) — pure functions over
  the store, no side effects.
- Reuse the BFF's `AppContext`/`ParquetStore` resolution; one MCP tool per stable endpoint.
- Auth/safety: booking/order tools require the same gate as the API; never expose a broker-send
  path. Note interactively-authenticated MCP servers may be absent in headless/cron runs.

## Links

Sits on top of `apps/frontend` (the API). Parked behind the front + capture hardening work.
