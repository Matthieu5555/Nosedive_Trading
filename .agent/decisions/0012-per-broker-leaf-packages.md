# 0012 — Per-broker leaf packages: one `infra-<broker>` package per broker

- **Status:** accepted
- **Date:** 2026-06-02
- **Source:** Vincent's ADR-018 (extending ADR-016 to a uniform rule); merged 2026-06-05

## Context

The blueprint mandates that `infra` be strategy-agnostic and broker-agnostic: it holds the IV,
surface, pricing, and risk engines plus normalized protocols (`BrokerTransport`, `MarketDataAdapter`,
`BrokerTick`, `FeedFault`). But broker connectivity and wire-format normalization are inherently
broker-specific. Leaving them in `infra/` would couple all consumers of `infra` to broker SDKs
(e.g. `httpx`, `websockets`, `ib_async`) regardless of which broker they use, and would blur the
agnostic/specific boundary the blueprint draws.

Three brokers are in scope — IBKR, Deribit, Saxo — each with different auth flows, API shapes, and
dependencies. A case-by-case solution produces ad hoc packages with no uniform rule.

## Decision

1. **One leaf package per broker**, all at the same dependency level, consuming `infra` and never
   the reverse:

   ```
   core  ←  infra  ←  infra-ibkr
                   ←  infra-deribit
                   ←  infra-saxo
                   ←  strategy  ←  execution
          +  apps/frontend (cross-package)
   ```

2. **`infra/` retains the broker-agnostic protocols** (`BrokerTransport`, `MarketDataAdapter`,
   `BrokerTick`, `FeedFault`) and all analytics. Each `infra-<broker>` package implements those
   protocols and exposes no analytics of its own.

3. **Naming is `infra-<broker>`**, not `infra-crypto` or any generic grouping. The name signals the
   exact broker; consistency makes the pattern predictable across all three packages.

4. **Import linter enforces the direction.** No `infra-<broker>` package is ever imported by
   `infra`, `strategy`, or `execution`. `strategy` imports `infra` for analytic contracts; broker
   adapters are wired at the app/runner layer.

5. **`uv sync` installs a broker's SDK only for consumers of that leaf package.** The `infra` test
   suite runs broker-free; no SDK is a transitive dependency of `infra` itself.

## Consequences

Extends ADR 0001 (monorepo layout): the leaf-package rule applies to broker packages. Supersedes
any naming where IBKR code was inside `infra/` directly — extraction to `infra-ibkr` is the target
state. ADR 0008 (IBKR adapter) describes the IBKR-specific choices that remain valid; this ADR
provides the structural container.
