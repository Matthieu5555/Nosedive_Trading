# Connectivity & deployment guides

Operator-facing guides for connecting a market-data provider, capturing an option chain on a
schedule, and running the collector unattended on a server. These are procedure docs, distinct from
the five lifecycle [runbooks](../runbooks/): the runbooks tell you how to operate a *running* system;
these tell you how to *connect a provider* and *stand collection up* in the first place.

- [connect-providers.md](connect-providers.md) — broker-by-broker (Deribit, Saxo, IBKR): the
  one-time setup, the smoke test, and the capture command, with verified entitlement-wall meanings.
- [capture-forward.md](capture-forward.md) — the scheduled free-data path: capture each market day
  live, reconstruct it offline; cron / Task Scheduler recipes for the idempotent, partition-by-day
  CLIs.
- [server-deployment-plan.md](server-deployment-plan.md) — DRAFT plan for unattended paper-mode
  collection against a headless IB Gateway on a shared server (Docker, security model, to-do list).

> **Provenance.** All three were ported from the pre-merge reference tree on 2026-06-05 and
> re-pointed to the current monorepo layout. Where a referenced script or config no longer exists
> (the broker connector/capture scripts are not yet relocated into the canonical `scripts/`; the
> flat `broker.yaml`/`collectors.yaml` are superseded by provider-scoped config), the doc says so
> inline rather than inventing a replacement. The load-bearing content — broker facts, the
> idempotent-capture contract, the security model — is current.

The authoritative per-broker facts (streaming URLs, payload shapes, IV units, entitlement walls)
live next to the code in `packages/infra-{saxo,ibkr,deribit}/README.md`.
