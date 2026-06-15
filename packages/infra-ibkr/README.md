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
  CP Gateway (`https://localhost:5000`, self-signed cert). `_client` injectable for tests. The
  429/503 retry rides tenacity (audit M20) with the unchanged cadence — `Retry-After` honoured
  when sane, else `backoff_base * 2**retry_index`, injected `sleep` — and a failed call raises
  `CpRestTransportError` carrying `status_code` directly (no `__cause__` reach). The
  transport-seam protocol (`SupportsRestGet` / `SupportsRest`) every collector types its injected
  transport with is re-exported here; its one definition lives in
  `algotrading.infra.collectors.transport_seam` (audit M40, shared with the Saxo leaf).
- `connectivity/cp_rest_session.py` — `CpRestSession`: the brokerage-session lifecycle TWS hid —
  `/iserver/auth/status` + a daemon-thread `/tickle` keepalive (~60 s; the session dies after ~5 min
  of silence). A dropped session fires `on_drop`, the engine's reconnect signal;
  `reauthenticate()` (POST `/iserver/reauthenticate`) revives a lapsed brokerage session without
  a fresh login — the self-heal `scripts/eod_babysitter.py` rides.
- `collectors/cp_rest_wire.py` — the typed CP wire shapes (pydantic v2, `extra="ignore"`): one
  model per payload (`SnapshotRow`, `SecdefSearchRow`, `StrikesPayload`, `SecdefInfoRow`,
  `HistoryBarRow`) with the bespoke broker-scalar coercers moved **verbatim** into
  `BeforeValidator` types (`parse_field_value` is hash-gated — it feeds persisted events), so
  every collector consumes one validated shape instead of `isinstance`-spelunking `Any`.
- `collectors/cp_rest_normalize.py` — `snapshot_to_events`: a validated `SnapshotRow` (CP field
  tags `84`→bid, `86`→ask, `88`/`85`→sizes, `31`→last, `7059`→last size; the `-1` sentinel
  dropped) → `RawMarketEvent`; one normalizer serves both the REST snapshot and the WS frame.
- `collectors/cp_rest_snapshot.py` — the shared snapshot engine: URI-safe conid batching (the
  HTTP-414 fix) + cold-snapshot warm-up polling, used by **both** the live adapter and the EOD
  close capture, so neither path re-rolls a bare single-shot request.
- `collectors/cp_rest_discovery.py` — `CpRestDiscovery`: the mandatory `secdef/search → strikes →
  info` sequence (the `name` field **omitted** on search, or strikes are suppressed).
- `collectors/cp_rest_adapter.py` — `CpRestMarketDataAdapter`: REST `snapshot()` (through the
  shared snapshot engine — batched, warm-up polled) + WS frame handling → `RawMarketEvent`.
  **Read-only** — only `/iserver/marketdata/*` is ever touched, never an order endpoint (asserted
  in `test_cp_rest_adapter.py`).

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
  timeouts, the cap, established-wait, retry/backoff), validated through frozen, strict pydantic
  models (the REP6 config seam); a rejected field raises a labeled `IbkrHistoryConfigError`.
  Secrets (consumer key/secret, the Live Session Token) stay in `.env`, never here (C7
  discipline).

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
  itself, so the registry's `conid: 0` placeholder is **unused** on the live path. The sibling
  `option_months_for_conid` reads the listed option months for an *already-resolved* underlying
  conid (a pinned constituent, or one resolved by a `STK` search) by a **conid-keyed** search, so
  a pinned ambiguous ticker still reads the right months (`test_cp_rest_index.py`).
- `collectors/cp_rest_close_capture.py` — `collect_target_basket` (the underlying-generic capture
  body) + `collect_live_basket` (the index wrapper) + the `CaptureTarget` descriptor. The capture
  **orchestration**, factored over a small `CaptureTarget` (symbol / search-symbol / exchange /
  currency / sec-type / conid) so the index lane and the constituent lane share it byte-for-byte:
  resolve conid → snapshot spot → discover + `plan_chain` the option chain → cap with
  `select_capture_keys` → snapshot the selected contracts at the close → assemble the
  `IndexBasket` `run_analytics` consumes. Every event is stamped at the index's own
  `session_close`; a snapshot row stamped *after* the close is dropped (no look-ahead)
  (`test_cp_rest_close_capture.py`). The economic 30Δ delta-band selection runs downstream in the
  analytics over the captured set. The snapshot mechanics live in `cp_rest_snapshot.py` (above);
  the window policy lives in `cp_rest_chain_window.py` (below).
- `collectors/cp_rest_constituent_capture.py` — `collect_index_and_constituents_basket`: widens
  the close capture to the index's **point-in-time top-N constituents by index weight** (the S1
  dispersion / implied-correlation input, TARGET §7.4). It captures the index leg (the spine), then
  resolves the top-N by weight (`UniverseConfig.constituent_top_n`, from 1A membership — never a
  hand-set list), resolves each constituent's equity conid (verified `constituent_conids` pins
  first, then a `STK` search — the OHLC-backfill pattern), and captures each constituent's chain
  over the *same* grid / close instant via `collect_target_basket`, merging all underlyings into
  one `IndexBasket`. A constituent that lists no options or fails to resolve is logged and
  **skipped** — one bad name never aborts the fire. The analytics engine is already
  underlying-generic, so this is a *capture-scope* widening with no engine change. The membership
  **top-N seam** is currently a local stand-in (`_top_n_by_weight`) pending the parallel
  `infra-sx5e-weighted-membership` resolver — see the WIRING note in the module
  (`test_cp_rest_constituent_capture.py`).
- `collectors/cp_rest_chain_window.py` — the discovery-window policy: `MMMYY` month-token
  parsing/bracketing (tenor-targeted discovery reaching the 2y/3y long end) and the
  **delta-driven, tenor-aware strike qualification** (T-delta-window): per expiry it qualifies
  the listed strikes that *contain* the 30Δ band at that tenor
  (`universe.select_discovery_strikes`, sized from the index spot and the conservative
  `strike_selection.discovery_working_vol`), so the band — whose strike width grows with √T — is
  delivered in full instead of clipped to ~ATM±1% by a fixed strike count. There is no strike cap
  (a cap would be the same intent-vs-delivery bound the fix removed); the only backstop is a
  fail-loud runaway valve (`DiscoveryRunawayError`) set far above any real listing.
- `live_capture.py` — `live_basket_source`: the explicit, logged live-vs-empty selection. A
  credentialed environment acquires an LST, builds the OAuth-signed transport, opens the
  brokerage session, and returns a `collect_live`-backed `BasketSource`; a non-credentialed one
  returns `None` so the runner falls back to its empty no-capture source (clean exit 0). When a
  `store` is passed (the production shim threads the runner's store) the bound source captures the
  **index + its top-N constituents** (`collect_index_and_constituents_basket`, reading the as-of
  membership from the store); with no store it captures the index only (the prior behaviour). The
  full path (auth-from-env → conid → capture → persisted grid) and the fallback are pinned in
  `test_live_capture_spine.py`; the store-wired constituent routing in
  `test_cp_rest_constituent_capture.py`.
- `live_capture.py` — `gateway_basket_source`: the **local CP Gateway** counterpart for when the
  Self-Service OAuth portal will not enrol (the "Enable OAuth Access → 400 not authenticated"
  wall). Keyed on the `IBKR_CP_GATEWAY` opt-in flag (not the `IBKR_CP_*` artifacts): it builds +
  establishes a **cookie-session** transport over the browser-logged-in `clientportal.gw`
  (`session_factory.build_gateway_session`, `oauth_signer=None`) and binds the *same* `collect_live`
  capture over it — no OAuth crypto, an attended path (the Gateway cookie lapses ~daily). Not set →
  `None`, so the `scripts/eod_run.py` shim falls through to the OAuth source, else the empty default.
  The flag gate, the bind-and-capture, and the establish handshake are pinned in
  `test_gateway_capture.py`.

### Read-only account read-path — positions / cash / fills (TARGET §5.9/§6, recon foundation)

The IBKR-side capability broker **reconciliation** reads from. The leaf had market-data ingestion
only; this is the strictly-read account collector the recon sub-lane of
`execution-operational-hardening` consumes (it feeds the broker side only — the diff/tolerance/
alert logic lives there, not here). It reuses the existing transport + session machinery; no new
transport.

- `collectors/cp_rest_account_wire.py` — the typed CP **account** wire shapes (pydantic v2,
  `extra="ignore"`, the `cp_rest_wire.py` pattern): `PositionRow` (`/portfolio/{accountId}/
  positions/{pageId}`), `LedgerRow` (`/portfolio/{accountId}/ledger`, one entry per currency +
  the synthetic `BASE`), `TradeRow` (`/iserver/account/trades`). Broker spellings (`avgCost`,
  `mktPrice`, `cashbalance`, `trade_time_r`) alias onto house names; the conid coercer is reused
  verbatim from `cp_rest_wire`. A malformed *row* is a `ValidationError` the collector records,
  never a coerced zero.
- `collectors/cp_rest_account.py` — `CpRestAccountCollector` / `collect_broker_account`: reads the
  three endpoints and normalizes into the frozen-seam contracts `BrokerPosition` /
  `BrokerCashBalance` / `BrokerFill`, bundled into one `BrokerAccountSnapshot` (`infra.contracts`).
  **Read-only** — only `/portfolio/*` and `/iserver/account/trades` GETs are ever touched, never
  an order endpoint (asserted in `test_cp_rest_account.py`, mirroring the market-data adapter's
  read-only assertion). Positions keep a **signed** quantity (short negative); fills are stamped
  at their **own venue time** (`trade_time_r` epoch-ms), never the read clock — no look-ahead. The
  transport, clock (`now_fn`), and the conid→`contract_key` resolver are injected (DI); the
  resolver defaults to `conid=<N>` (recon joins on the broker's conid). It does **not** submit
  orders, persist, or run recon — it hands back the typed snapshot. Lives in CI against a fake
  transport; live bring-up is a smoke run, not pytest.

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
  persisted grid, plus the no-look-ahead drop and the no-credentials fallback), and the
  **read-only account collector** (`cp_rest_account` against a fake transport: positions/cash/
  fills normalize into the broker contracts, the fill's own-venue-time stamp, the malformed-row
  rejection, and the read-only/no-order-endpoint assertion) all run in CI against fakes — the
  verifiable core.
- The Nautilus config builder's guard runs; its construction test skips without the `ibkr` extra.
- Live runs are a smoke script on a machine with the relevant Gateway, not pytest. Install the
  Nautilus-TWS path with `uv sync --extra ibkr`; the REST path needs the CP Gateway running locally.

## Superseded (deleted)

The hand-rolled `ib_async` modules (`connectivity/ibkr_transport.py`,
`collectors/ibkr_adapter.py`, `collectors/ibkr_discovery.py`, vendored per ADR 0022) were
superseded by the two live transports (CP-REST + Nautilus-TWS, ADR 0023/0024) and have been
**deleted** along with their `importorskip("ib_async")` tests (2026-06 maintainability audit,
M21). The cross-broker shape test (`infra-deribit/tests/test_broker_agnostic.py`) now exercises
IBKR through the SDK-free `snapshot_to_events`, so it runs unconditionally in the gate. Real
captured samples for the gate's SDK-free replay test:
`samples/{spy_real_2026-06-04,asml_real_2026-06-05}.json`.
