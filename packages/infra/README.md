# infra — the volatility infrastructure layer (level 1)

`algotrading.infra`: strategy-agnostic market plumbing — capture, the instrument master,
the pure analytics core, risk, QC, the Nautilus-hosted actor, orchestration, and storage.
Built on `algotrading.core` and the frozen `infra.contracts` seam. **This layer never
imports a layer above it** (strategy, execution, frontend); import-linter enforces it.

This README is a routing hop. Each module below has its own `README.md` next to its code;
read that for the detail.

## The frozen seam

- **`contracts/`** — the typed data contracts every other workstream imports and the one
  `StorageRepository` protocol. **M0 owns it; nobody edits it in place** — a change is a
  request routed through M0, because every field ripples outward. Start here to understand
  what crosses a module boundary.

## Market-data plane (capture)

- **`connectivity/`** — session lifecycle, the `SessionSupervisor` (the one reconnect
  home: backoff, client-id convention, gap recovery), clocks.
- **`universe/`** — resolve broker chains → canonical `InstrumentMaster` rows, the
  `ChainSelection` selection policy, the read-side `UniverseService`.
- **`collectors/`** — the one push `RawCollector` that normalizes each broker tick into
  the canonical `contracts.RawMarketEvent` and persists it idempotently (ADR 0027).

## Analytics core (pure functions)

`snapshots/` → `forwards/` → `iv/` → `surfaces/` → `pricing/` are the bespoke math: raw
events to a quality-labeled market state, the parity forward, the IV solve, the SVI
surface fit with no-arb checks, and Black-76/American pricing + Greeks. All pure — no I/O,
no clock, no RNG; everything is injected and stamped. Read `iv/README.md` and
`surfaces/README.md` before touching them. `utils/` holds shared
numeric helpers.

## Risk

- **`risk/`** — portfolio Greeks, monetized (dollar) sensitivities, aggregation,
  broker reconciliation, and the versioned scenario grid (ADR 0006). It never prices — it
  calls the frozen pricer.

## QC and validation

- **`qc/`** — the ten named checks + anomaly detection; a failure names the exact failing
  object (maturity / quote / underlying / solver).
- **`validation/`** — the anomaly/triage plane; qc/validation/anomaly all feed one
  persisted `triage_records` table (ADR 0010).

## The operable layer

- **`actor/`** — the thin Nautilus `Actor` that drives the pure `run_analytics` over a
  `RawMarketEvent` stream, live and replay, on Nautilus's clock (ADR 0023/0025). The
  byte-identical-replay invariant lives here.
- **`orchestration/`** — jobs (`collect_live`, `build_surface`, the EOD `pipeline`),
  `qc_job`, the five metrics, four alerts, the dashboard, the run-state ledger, and the
  `reconstruction/` subpackage (replay/backfill over the same compute path).
- **`observability/`** — run-lineage over the `RunRegistry`.
- **`storage/`** — `ParquetStore` and the tiered `StorageRepository` backends (SQLite /
  Postgres run registry) behind the M0 port (ADR 0015).

## Broker leaves

The per-broker adapters live in sibling packages, not here:
`packages/infra-{ibkr,saxo,deribit}`. They import `infra` (+ `core`) and emit
`collectors.BrokerTick` onto the unified seam.

## Verify

```
uv run ruff check packages/infra/src
uv run mypy .
uv run pytest packages/infra/tests -q
```

The whole system is one gate from the repo root — see `AGENTS.md`.
