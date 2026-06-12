# infra-saxo

Saxo Bank OpenAPI leaf adapter. Owner: **M5 — broker adapters**. Imports `algotrading.infra` +
`algotrading.core`, nothing above (enforced by import-linter).

## What it does

- `auth/` — the Saxo OAuth2 lifecycle on Authlib: `web_oauth` (authorize URL + code exchange via
  `OAuth2Client`, credentials in the body as Saxo expects), `TokenManager` (the bespoke part —
  proactive background refresh against Saxo's 20/40-minute token lifetimes, rotation hook, expiry
  guard; the wire grant itself is Authlib's), `token_persist` (restart-resilient `.env` upsert via
  python-dotenv `set_key`; a missing `.env` is a logged no-op, never created). **Secrets never
  enter git** — tokens live in `$HOME`/`.env`.
- `connectivity/saxo_transport.py` — `SaxoTransport`: stateless REST (httpx, one `_request` core
  for all verbs) + streaming-WS URL builder; a caller-supplied `token_fn` keeps auth out of the
  wire layer.
- `connectivity/ws_listener.py` — thin re-export of `WebSocketListener`, the shared WS
  lifecycle (owned thread, stop event, reconnect with backoff, fault callback). The single
  implementation lives in `algotrading.infra.collectors.ws_listener` (the former byte-identical
  twin here was hoisted there — audit M26).
- `collectors/saxo_discovery.py` — symbol → `OptionContract` list via `contractoptionspaces`.
- `collectors/saxo_adapter.py` — options-chain streaming snapshot/delta frames → `BrokerTick` EAV
  (binary frame parser, exact per-expiry strike routing via the canonical key parser, Index-map
  delta routing, ATM-centred expiry windows).
- `collectors/saxo_underlying.py` — low-frequency InfoPrices spot probe for the reference spot.

## Dependencies

`httpx` (REST) and `authlib` (OAuth2) are hard deps; `websockets` is imported lazily only when
streaming is active. No broker SDK and no secret is needed to import the package or run the test
suite.

## Status / caveats

**Direction set by ADR 0023 (2026-06-05):** Nautilus ships no Saxo adapter, so **this leaf is a
survivor — kept**. It implements the `algotrading.infra.collectors.MarketDataAdapter` seam and feeds
the catalog Nautilus replays through the one unified `RawCollector` (ADR 0027 / C6: the pull
`contracts.BrokerSession` seam has been retired). There is no `flow.py`: `SaxoMarketDataAdapter`
emits `collectors.BrokerTick` onto the unified push collection seam (ADR 0027) and orchestration
drives the analytics pipeline — there is no per-broker flow façade. OAuth refresh/persist/expiry are tested against a fake auth server; real
sample: `samples/asml_real_2026-06-04.json`.
