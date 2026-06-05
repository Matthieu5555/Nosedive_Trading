# infra-ibkr

Interactive Brokers leaf adapter. Imports `algotrading.infra` + `algotrading.core`, nothing
above (enforced by import-linter).

## What it does (ADR 0023/0025)

IBKR rides **Nautilus's shipped InteractiveBrokers adapter** — Nautilus is the runtime spine, and
its adapter is the live transport. This leaf is the thin seam around it:

- `connectivity/nautilus_ibkr.py` — `build_data_client_config(...)`: builds the Nautilus
  `InteractiveBrokersDataClientConfig` (host/port/client-id, real-time or delayed market data,
  instruments to load). Import-guarded on the `ibkr` extra: without it, raises
  `IbkrExtraNotInstalled` with an actionable message instead of an opaque `ModuleNotFoundError`.
- `collectors/nautilus_normalize.py` — `quote_tick_to_events` / `trade_tick_to_events`: the pure
  seam that turns the `QuoteTick`/`TradeTick` the adapter delivers into our immutable
  `RawMarketEvent` rows (one per observed field), content-addressed by `content_event_id` so a
  re-delivered tick (same `sequence`) is written exactly once. Our `ParquetStore` stays the system
  of record (ADR 0025); no broker SDK type crosses out of this seam.

## The `ibkr` extra and what runs in CI

The `ibkr` extra is `nautilus-trader[ib]` (pulls `nautilus-ibapi`). It is **not** in the gate env
(ADR 0018), and a live connect needs a running TWS / IB Gateway, which CI does not have. So:

- the **normalizer** (`nautilus_normalize`) uses only Nautilus *base* tick types and is fully tested
  in CI — it is the verifiable core of the IBKR-on-Nautilus data path;
- the **config builder** is tested two ways: the guard (no extra → clear error) always runs; the
  construction test skips unless the extra is present;
- install the live path with `uv sync --extra ibkr` and run it on a machine with a Gateway.

## Open: IBKR-over-REST course requirement

Nautilus's InteractiveBrokers adapter is **TWS/IB-Gateway-only** (no Client Portal / REST option).
A course requirement mandates an IBKR **REST** connection; [ADR 0024](../../.agent/decisions/0024-ibkr-rest-transport-alongside-tws.md)
(**proposed**, pending owner ruling) records the resolution — a custom IBKR-REST connector into the
catalog (the Saxo/Deribit pattern) *alongside* this Nautilus-TWS path, switched by config. The seam
here does **not** foreclose that: the normalizer takes plain tick inputs and a REST connector would
feed the same `RawMarketEvent` raw layer. Do not hard-retire the REST option when extending this leaf.

## Superseded

The hand-rolled `ib_async` modules (`connectivity/ibkr_transport.py`,
`collectors/ibkr_adapter.py`, `collectors/ibkr_discovery.py`, vendored per ADR 0022) are
**superseded** by the Nautilus adapter (ADR 0023). They are kept as files — reached only by direct
import, no longer surfaced from the package `__init__`, and their tests `importorskip("ib_async")`
— until **C5** removes them. Real captured samples used by the gate's SDK-free replay test:
`samples/{spy_real_2026-06-04,asml_real_2026-06-05}.json`.
