# infra-saxo

Saxo Bank OpenAPI leaf adapter. Owner: **M5 — broker adapters**. Imports `algotrading.infra` +
`algotrading.core`, nothing above (enforced by import-linter).

## What it does

- `auth/` — the full OAuth2 lifecycle: `web_oauth` (authorize URL + code exchange), `TokenManager`
  (background refresh, refresh-token rotation, expiry guard), `token_persist`/`env_tokens`
  (restart-resilient `.env` upsert). **Secrets never enter git** — tokens live in `$HOME`/`.env`.
- `connectivity/saxo_transport.py` — `SaxoTransport`: stateless REST (httpx) + streaming-WS URL
  builder; a caller-supplied `token_fn` keeps auth out of the wire layer.
- `collectors/saxo_discovery.py` — symbol → `OptionContract` list via `contractoptionspaces`.
- `collectors/saxo_adapter.py` — options-chain streaming snapshot/delta frames → `BrokerTick` EAV
  (binary frame parser + Index-map delta routing + ATM-centred expiry windows).
- `collectors/saxo_underlying.py` — low-frequency InfoPrices spot probe for the reference spot.

## Dependencies

`httpx` (REST) is a hard dep; `websockets` is imported lazily only when streaming is active. No
broker SDK and no secret is needed to import the package or run the test suite.

## Status / caveats

**Direction set by ADR 0023 (2026-06-05):** Nautilus ships no Saxo adapter, so **this leaf is a
survivor — kept**. It implements the `algotrading.infra.collectors.MarketDataAdapter` seam and feeds
the catalog Nautilus replays through the one unified `RawCollector` (ADR 0027 / C6: the pull
`contracts.BrokerSession` seam has been retired). `flow.py` is deferred until the analytics pipeline
lands in `packages/infra`. OAuth refresh/persist/expiry are tested against a fake auth server; real
sample: `samples/asml_real_2026-06-04.json`.
