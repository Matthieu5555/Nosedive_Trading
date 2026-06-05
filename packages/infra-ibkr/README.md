# infra-ibkr

Interactive Brokers leaf adapter. Imports `algotrading.infra` + `algotrading.core`, nothing
above (enforced by import-linter).

## Two ingestion paths, one `RawMarketEvent` (ADR 0023/0024/0025)

IBKR has **two** market-data paths; both normalize into our immutable `RawMarketEvent` (the system
of record — ADR 0025), so the actor host replays either identically. The path is chosen by config
(`select_ibkr_transport`); **REST is preferred**, Nautilus-TWS is the manual-flip fallback (ADR
0024 §2, no automatic failover). Both build events through the shared `collectors/market_fields.py`
helper, so they emit **byte-identical** rows for the same observation — the equivalence bar proven
in `tests/test_cp_rest_equivalence.py`.

### Client Portal REST/WebSocket (preferred — the course requirement, ADR 0024)

A custom adapter over IBKR's Client Portal Web API (the Saxo/Deribit pattern — `httpx`/`websockets`,
real deps):

- `connectivity/cp_rest_transport.py` — `CpRestTransport`: REST verbs + the WS URL over the local
  CP Gateway (`https://localhost:5000`, self-signed cert). `_client` injectable for tests.
- `connectivity/cp_rest_session.py` — `CpRestSession`: the brokerage-session lifecycle TWS hid —
  `/iserver/auth/status` + a daemon-thread `/tickle` keepalive (~60 s; the session dies after ~5 min
  of silence). A dropped session fires `on_drop`, the engine's reconnect signal.
- `collectors/cp_rest_normalize.py` — `snapshot_to_events`: CP market-data field tags
  (`84`→bid, `86`→ask, `88`/`85`→sizes, `31`→last, `7059`→last size) → `RawMarketEvent`, dropping
  the `-1` sentinel; one normalizer serves both the REST snapshot and the WS frame.
- `collectors/cp_rest_discovery.py` — `CpRestDiscovery`: the mandatory `secdef/search → strikes →
  info` sequence (the `name` field **omitted** on search, or strikes are suppressed).
- `collectors/cp_rest_adapter.py` — `CpRestMarketDataAdapter`: REST `snapshot()` + WS frame
  handling → `RawMarketEvent`. **Read-only** — only `/iserver/marketdata/*` is ever touched, never
  an order endpoint (asserted in `test_cp_rest_adapter.py`).

### Nautilus TWS (fallback, ADR 0025)

- `connectivity/nautilus_ibkr.py` — `build_data_client_config(...)`: the Nautilus
  `InteractiveBrokersDataClientConfig`, import-guarded on the `ibkr` extra
  (`IbkrExtraNotInstalled` when absent).
- `collectors/nautilus_normalize.py` — `quote_tick_to_events` / `trade_tick_to_events`: Nautilus
  `QuoteTick`/`TradeTick` → `RawMarketEvent`.

## What runs in CI

The gate is **broker-free**: no live CP Gateway, no TWS Gateway, no live socket, no secrets.

- The **normalizers**, **discovery**, **session keepalive** (fake clock/transport), the adapter's
  **REST snapshot** + **WS frame** handling, and the **REST↔TWS equivalence** test all run in CI
  against fakes — the verifiable core.
- The Nautilus config builder's guard runs; its construction test skips without the `ibkr` extra.
- Live runs are a smoke script on a machine with the relevant Gateway, not pytest. Install the
  Nautilus-TWS path with `uv sync --extra ibkr`; the REST path needs the CP Gateway running locally.

## Superseded

The hand-rolled `ib_async` modules (`connectivity/ibkr_transport.py`,
`collectors/ibkr_adapter.py`, `collectors/ibkr_discovery.py`, vendored per ADR 0022) are
**superseded** (ADR 0023). They are kept as files — reached only by direct import, not surfaced
from the package `__init__`, tests `importorskip("ib_async")` — until **C5** removes them. Real
captured samples for the gate's SDK-free replay test:
`samples/{spy_real_2026-06-04,asml_real_2026-06-05}.json`.
