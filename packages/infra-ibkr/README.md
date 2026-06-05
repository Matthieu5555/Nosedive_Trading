# infra-ibkr

Interactive Brokers leaf adapter. Owner: **M5 — broker adapters**. Imports `algotrading.infra`
+ `algotrading.core`, nothing above (enforced by import-linter).

## What it does

Turns a TWS / IB Gateway connection into broker-agnostic market data:

- `connectivity/ibkr_transport.py` — `IbkrTransport` over `ib_async` (connect + timeout-bounded
  round-trip), behind the `algotrading.infra.connectivity` `BrokerTransport` seam.
- `collectors/ibkr_discovery.py` — `IbkrUniverseDiscovery`: resolve the underlying conId, then
  `reqSecDefOptParams` → broker-agnostic `OptionParams`.
- `collectors/ibkr_adapter.py` — `IbkrMarketDataAdapter`: ib_async ticker callbacks → `BrokerTick`
  (delayed market-data type by default, IB error codes classified entitlement/pacing/other).

Free delayed data needs no live entitlement; a paid upgrade is a constructor arg, not a code edit.

## ib_async is an optional extra

`ib_async` is **not** in the gate env (ADR 0018): the adapter/transport/discovery import it at
module load, so importing this package's collectors needs it, and the live-wiring tests
`importorskip("ib_async")`. Install the live path with `uv sync --extra ibkr`. The committed-sample
replay test needs no SDK and runs in the gate.

## Status / caveats

Vendored near-verbatim per **ADR 0022** (which contests ADR 0020): the adapter implements the
`algotrading.infra.collectors.MarketDataAdapter` seam, not yet M0's thin `contracts.BrokerSession`.
`flow.py` (the analytics-pipeline orchestration) is deferred until that pipeline lands in
`packages/infra`. Real samples: `samples/{spy_real_2026-06-04,asml_real_2026-06-05}.json`.
