# 0013 — `infra-deribit`: Deribit as the first end-to-end broker, USD settlement

- **Status:** accepted
- **Date:** 2026-06-02
- **Source:** Vincent's ADR-016 (renamed infra-crypto → infra-deribit per ADR-018); merged 2026-06-05

## Context

IBKR requires a paid OPRA entitlement (code 354) for US options data — confirmed live.
A free, complete, full-surface data source was needed to validate the analytics chain end-to-end
before paying entitlements. Deribit offers a public API (REST + WebSocket) for BTC and ETH options,
a testnet, sufficient liquidity, and USD settlement (no native-coin accounting complexity). It is the
only major exchange with liquid crypto options meeting all those criteria simultaneously.

## Decision

1. **`packages/infra-deribit/` is the first live broker validated end-to-end.** It is a leaf package
   under ADR 0012 (one package per broker), consuming `infra` analytics and never the reverse.

2. **Underlying instruments: BTC and ETH options only.** USD-settled, strike in USD. Mark IV is
   Deribit's implied vol mark for each contract — used as input to `mark_iv_divergence` QC check.

3. **The package consumes the broker-agnostic protocols from `infra`** (`BrokerTransport`,
   `MarketDataAdapter`, `BrokerTick`). It does not re-define them.

4. **Dependency: `httpx` + `websockets` only.** No Deribit-specific SDK. Public REST for discovery,
   WebSocket for live tick streaming.

5. **`mark_iv_divergence` QC check** is defined in `infra/` (broker-agnostic), not in
   `infra-deribit`, because it operates on the normalized `BrokerTick` and compares the broker's
   mark IV to the platform's computed IV — a test that applies to any broker that publishes mark IV.

## Consequences

The end-to-end analytics chain (discovery → collection → QC → snapshots → forwards → IV → surface
→ risk) is proven and deterministic for BTC/ETH before any paid entitlement is required. IBKR
and Saxo adapters plug into the same protocols; `infra/` is untouched by broker additions.
