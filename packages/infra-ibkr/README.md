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

### Historical daily-OHLC backfill (ADR 0031) — unattended, OAuth 1.0a

The history path the live snapshot path lacks: years of underlying daily OHLC into the immutable,
provider-partitioned `DailyBar` table (WS 1C, Part C). Runs unattended over the hosted CP Web API
with **in-house OAuth 1.0a** (no TWS/IB Gateway, no daily interactive login).

- `connectivity/cp_rest_oauth.py` — the OAuth 1.0a per-request signer (referencing `ibind`, not
  depending on it): `signature_base_string` (RFC 5849 §3.4.1), `sign_hmac_sha256` (LST-keyed
  per-request signature), `sign_request`, `authorization_header` (with the IBKR `realm`), and
  `make_oauth_signer` — the factory that turns `OAuthCredentials` into the
  `(method, url, query) → headers` `OAuthSigner` the transport injects. Pure crypto, no
  clock/nonce read (both injected) → a hand-computed known-answer vector pins it
  (`test_cp_rest_oauth.py`). A missing/expired token raises a labeled `CpOAuthError`.
- `connectivity/cp_rest_lst.py` — **Live Session Token acquisition** on **pycryptodome** (the half
  the signer leaves out, ADR 0031 §2): the RSA-SHA256-signed `oauth/request_token`, the
  Diffie–Hellman exchange (`oauth/live_session_token`), and the LST derivation
  (`K = B^a mod p`; LST = base64(HMAC-SHA1(K, prepend))), validated against IBKR's returned
  signature. `build_signed_cp_rest_transport` is the **production entry point**: it runs the
  exchange, builds `make_oauth_signer` from the derived LST, and returns a `CpRestTransport` with
  that signer injected — so the production path (not just tests) signs every request. The two
  exchange POSTs are RSA-signed (the LST does not exist yet); the HTTP POST is injected so the
  gate drives a fake IBKR endpoint whose DH side is computed independently
  (`test_cp_rest_lst_production.py`). The network is never opened in pytest.
- `connectivity/cp_rest_transport.py` — extended with an optional `oauth_signer`: when set, every
  request carries the `Authorization: OAuth …` header (the hosted-endpoint path); left `None` it is
  the unchanged ADR 0024 local-Gateway cookie path.
- `connectivity/cp_rest_session.py` — extended with the brokerage-session open: `open_brokerage_
  session` (POST `ssodh/init`) and `wait_until_established` — blocks (injected sleep, no real wait)
  until the session reports `established: true`, raising `SessionNotEstablishedError` otherwise, so
  a history request is never fired into a not-yet-established session.
- `collectors/cp_rest_history_normalize.py` — `history_to_daily_bars`: a CP `marketdata/history`
  payload → `DailyBar` rows; each bar's `trade_date` is read from its **own** epoch-ms timestamp
  (no look-ahead). Malformed rows (`high < low`, close out of range, negative volume, NaN, missing
  field) are rejected with a labeled error at the normalize door.
- `collectors/cp_rest_history.py` — `CpRestHistoryCollector`: fetch + normalize + persist, hardened
  per ADR 0031 §5 — established-gated, warmup call, 5-concurrent cap, exponential-with-cap retry
  around maintenance windows, and **resumable** (`backfill` re-fetches only the missing tail;
  idempotent on `(provider, underlying, trade_date)`). **Read-only** — only
  `/iserver/marketdata/history`, never an order endpoint (`test_cp_rest_history.py`). Use a
  **dedicated second IBKR username** so the backfill never knocks out the live feed (one username =
  one brokerage session).
- `config.py` + `configs/ibkr_history.yaml` — the no-hardcode connectivity config (base URL,
  timeouts, the cap, established-wait, retry/backoff). Secrets (consumer key/secret, the Live
  Session Token) stay in `.env`, never here (C7 discipline).

### Live EOD close capture — `collect_live` (WS 1C, ADR 0024/0031)

The source that closes the broker→raw-event seam: the EOD runner (`algotrading.infra`, a layer
*below* this one) exposes a transport-agnostic `BasketSource` and a `basket_source` parameter on
`build_default_deps`/`default_stages_builder`; this leaf provides the live source and the
credential-driven selection. The runner never imports the broker leaf — the cross-layer wiring
lives only in the `scripts/eod_run.py` shim, which is outside the root gate.

- `connectivity/cp_rest_credentials.py` — the `.env` → `LstConsumer` loader the OAuth work
  flagged as missing. Reads the `IBKR_CP_*` artifacts (consumer key, access token + encrypted
  secret, the two RSA PEM **file paths**, DH prime, optional generator/realm) into the
  `LstConsumer` the LST flow needs, plus a real `httpx`-backed `post` for the two exchange
  endpoints. Absent creds → `None` (clean no-capture); partial creds → a labeled `CpOAuthError`
  (`test_cp_rest_credentials.py`).
- `collectors/cp_rest_index.py` — `resolve_index` / `resolve_index_conid`: resolve an index's
  conid (and its listed option months) from its symbol via `GET /iserver/secdef/search`
  (`secType=IND`, matched to the routing exchange CBOE/EUREX). The live path resolves the conid
  itself, so the registry's `conid: 0` placeholder is **unused** on the live path
  (`test_cp_rest_index.py`).
- `collectors/cp_rest_close_capture.py` — `collect_live_basket`: the real capture. Resolve conid
  → snapshot the index spot → discover + `plan_chain` the option chain → cap with
  `select_capture_keys` → snapshot the selected contracts at the close → assemble the
  `IndexBasket` `run_analytics` consumes. Every event is stamped at the index's own
  `session_close`; a snapshot row stamped *after* the close is dropped (no look-ahead)
  (`test_cp_rest_close_capture.py`). The economic 30Δ delta-band selection runs downstream in the
  analytics over the captured set.
- `live_capture.py` — `live_basket_source`: the explicit, logged live-vs-empty selection. A
  credentialed environment acquires an LST, builds the OAuth-signed transport, opens the
  brokerage session, and returns a `collect_live`-backed `BasketSource`; a non-credentialed one
  returns `None` so the runner falls back to its empty no-capture source (clean exit 0). The
  full path (auth-from-env → conid → capture → persisted grid) and the fallback are pinned in
  `test_live_capture_spine.py`.

### Nautilus TWS (fallback, ADR 0025)

- `connectivity/nautilus_ibkr.py` — `build_data_client_config(...)`: the Nautilus
  `InteractiveBrokersDataClientConfig`, import-guarded on the `ibkr` extra
  (`IbkrExtraNotInstalled` when absent).
- `collectors/nautilus_normalize.py` — `quote_tick_to_events` / `trade_tick_to_events`: Nautilus
  `QuoteTick`/`TradeTick` → `RawMarketEvent`.

## What runs in CI

The gate is **broker-free**: no live CP Gateway, no TWS Gateway, no live socket, no secrets.

- The **normalizers**, **discovery**, **session keepalive** + **established-wait** (fake
  clock/transport), the adapter's **REST snapshot** + **WS frame** handling, the **OAuth 1.0a
  signer** (known-answer vector), the **LST acquisition** (real RSA→DH→LST on pycryptodome
  against a fake endpoint whose DH side is computed independently) and the **production
  transport-signing** seam, the **history fetch/normalize/backfill-resume** path, the
  **REST↔TWS equivalence** test, and the **live EOD close capture** (`collect_live` against a
  fake gateway: credential load → conid resolve → chain plan → close snapshot → basket →
  persisted grid, plus the no-look-ahead drop and the no-credentials fallback) all run in CI
  against fakes — the verifiable core.
- The Nautilus config builder's guard runs; its construction test skips without the `ibkr` extra.
- Live runs are a smoke script on a machine with the relevant Gateway, not pytest. Install the
  Nautilus-TWS path with `uv sync --extra ibkr`; the REST path needs the CP Gateway running locally.

## Superseded

The hand-rolled `ib_async` modules (`connectivity/ibkr_transport.py`,
`collectors/ibkr_adapter.py`, `collectors/ibkr_discovery.py`, vendored per ADR 0022) are
**superseded** by the two live transports (CP-REST + Nautilus-TWS, ADR 0023/0024). They are
**not** wired into `select_ibkr_transport` and are not surfaced from the package `__init__` —
reached only by direct import, and their tests `importorskip("ib_async")`. They are retained
only as dead reference for now; deleting them is a loose cleanup (it was *not* part of C5, which
retired the flat `backend/` tree, only). Real captured samples for the gate's SDK-free replay
test: `samples/{spy_real_2026-06-04,asml_real_2026-06-05}.json`.
