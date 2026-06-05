# infra-deribit

Deribit (crypto options) leaf adapter. Owner: **M5 — broker adapters**. Imports `algotrading.infra`
+ `algotrading.core`, nothing above (enforced by import-linter).

## What it does

- `connectivity/deribit_transport.py` — `DeribitTransport`: public REST (httpx) for discovery +
  index price, async WebSocket subscribe for live ticks (`websockets`, lazy-imported). No auth —
  market data is public.
- `collectors/deribit_discovery.py` — `/public/get_instruments` + the pure
  `parse_deribit_instrument_name` (`BTC-25JUL25-100000-C` → canonical `OptionContract`).
- `collectors/deribit_adapter.py` — WebSocket ticker frames → `BrokerTick` EAV, with the crypto
  specifics: base-currency (BTC/ETH) option prices are multiplied by the USD index price so every
  canonical tick is USD-denominated; `mark_iv` (Deribit's own IV, as a percentage) is carried for
  downstream divergence QC.

## Crypto conventions

Multiplier 1 (one contract = 1 BTC/ETH, cash-settled), currency USD (index-priced, not native
coin), `security_type=CRYPTO` on the underlying. Short-dated expiries (from 1 day) are allowed.

## Dependencies

`httpx` is a hard dep; `websockets` is imported lazily only when streaming. No SDK or secret is
needed to import the package or run the test suite.

## Status / caveats

**Direction set by ADR 0023 (2026-06-05):** Nautilus ships no Deribit adapter, so **this leaf is a
survivor — kept**. It implements the `algotrading.infra.collectors.MarketDataAdapter` seam and feeds
the catalog Nautilus replays through the one unified `RawCollector` (ADR 0027 / C6: the pull
`contracts.BrokerSession` seam has been retired). `flow.py` is deferred until the analytics pipeline
lands in `packages/infra`. No real sample is carried (Deribit captures are synthetic in
`tests/conftest.py`).
