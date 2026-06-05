# Glossary — domain vocabulary

Terms specific to this work, with one-line definitions, so an agent doesn't guess
and propagate a wrong guess into code. This is a **seed**: add a term the moment
you notice an agent (or a human) misread it. One line each; link to a deeper doc
only if the term genuinely needs one.

Keep definitions to what is established. If a term's meaning here is
project-specific and not yet settled, write "TODO: define" rather than inventing
a plausible-sounding definition — a confident wrong entry is worse than a gap.

## Quant / finance

- **As-of date** — the simulated "now" for a computation: data is read as it was
  known at that date, never with later information. The core defense against
  look-ahead bias.
- **Look-ahead bias** — using information in a backtest that would not have been
  available at the decision time being simulated.
- **Point-in-time data** — the data vintage actually known at a past date,
  including the values before any later restatement or revision.
- **Realized volatility** — volatility estimated from observed historical
  returns over a window, as opposed to implied (forward-looking, option-derived).

## Project-specific

These come from the volatility-infrastructure roadmap. Each definition is grounded
in the code that uses the term; the deeper detail lives in the named module's
`README.md`. One name per concept — if you reach for a synonym, use the term here
instead.

### Identity and provenance

- **Instrument key (canonical key)** — the nine-field economic identity of a
  tradable instrument (`contracts.InstrumentKey`), collapsed by `canonical()` into
  a deterministic pipe-joined string. That string is the primary key derived
  records store and join on; it is built by hand, never from a salted `hash()`, so
  it is byte-identical across machines and processes.
- **Broker contract id (`conId`)** — the broker's external id for a contract; one
  of the nine `InstrumentKey` fields, never the platform's sole identifier.
  Recoverable from the canonical key via `broker_contract_id_from_canonical`.
- **Content-addressed event id** — a raw event's identity, SHA-256 of
  `(instrument_key, field, sequence)`, so the same observation always hashes to the
  same id and re-delivery or restart dedups to exactly one write.
- **Provenance stamp** — the immutable `ProvenanceStamp` every derived record
  carries: its source records (by full key), source timestamps, calc time, code
  version, config hash, and a SHA-256 content hash. The mechanism behind the
  "provenance on everything" invariant.
- **Source record ref** — a typed pointer (`SourceRecordRef`) to one source row by
  table plus *full* canonical primary key, so lineage resolves to exactly one row.
- **Config hash** — SHA-256 of the config's canonical JSON, stamped onto derived
  records to tie a result to the exact economic settings that produced it.
  Environment settings (data root, hosts) deliberately stay out of it.
- **Table family / contract** — one of the twelve frozen dataclasses
  (`contracts.tables`) that may cross a workstream seam; its metadata (key, layer,
  append-only, provenance) lives in the registry `TableSpec`.

### Storage

- **Append-only layer / immutable raw layer** — the `raw` storage layer, where an
  existing primary key may never be overwritten; the on-disk form of the
  immutable-raw invariant. Captured ticks are written once and never edited.
- **Versioned partition (restatement)** — a `version=<V>` sub-partition holding a
  derived analytic recomputed under newer code, landing *beside* the live
  (unversioned) partition rather than overwriting it. A version-blind read
  (`read(version=None)`) returns live rows only (roadmap step 13, ADR 0007).
- **Run-state ledger** — the append-only JSON-lines record under the store root of
  which end-of-day stage finished cleanly for which trade date; the basis for
  idempotent restart and the dashboard's last-healthy / backlog facts.

### Market data

- **Gap event (meta-event)** — a raw event under a reserved `__`-prefixed field
  (e.g. `__gap__`, value = outage seconds) recording an *absence* of data rather
  than an observation; downstream code filters these out with `is_observation`.
- **Feed notice** — a classified broker feed-health signal (pacing / entitlement /
  other), logged and counted in the daily summary, deliberately *not* written into
  the observation stream.
- **Session id** — the collector's idempotency scope; stable across restarts
  (typically derived from the trade date) so a restarted collector recognizes
  already-written events.

### Analytics (Workstream C)

- **Reference spot** — the single labeled price chosen for an instrument at a
  snapshot instant, by the fixed ladder mid → last → close → carry_forward; the
  chosen rung is recorded in `reference_type`.
- **As-of read (inclusive boundary)** — a point-in-time read where events with
  `canonical_ts <= snapshot_ts` are usable and strictly-later ones are dropped as
  the future; order-independent, ties broken by `event_id`. The platform's
  look-ahead boundary.
- **Total variance** — `w = σ²·T`, implied vol squared times year-fraction to
  expiry; the space the SVI surface and IV points live in.
- **Log-moneyness** — `k = ln(K/F)`, strike relative to the forward; the x-axis of
  every smile.
- **Parity forward** — the forward `F` recovered from put-call parity
  `C − P = DF·(F − K)` read as a line across strikes, recovering `F` and `DF`
  jointly without an externally supplied discount factor.
- **SVI** — Stochastic-Volatility-Inspired; the five-parameter raw form
  `w(k) = a + b(ρ(k−m) + √((k−m)² + σ²))` fit to one maturity's total-variance smile.
- **Cost of carry (`b`)** — generalized-BSM carry: `b = r` for non-dividend equity,
  `b = 0` for a future (Black-76), `b = r − q` for continuous dividend yield `q`.
- **Calendar / butterfly arbitrage** — the two no-arbitrage diagnostics on a
  surface: calendar = total variance non-decreasing in maturity at fixed `k`;
  butterfly = Gatheral's `g(k) ≥ 0`. Both are reported as diagnostics, not enforced.

### Risk (Workstream D)

- **Line vs lot** — a *lot* is one `Position` row for a contract from a single
  source; a *line* is one position's risk row (`PositionRisk`) after all lots of
  that contract net together. The line *is* the contract.
- **Per-unit / position-level / dollar greek** — three scalings of one sensitivity:
  *per-unit* is straight from the pricer (one unit of underlying); *position-level*
  is `per_unit × multiplier × quantity`; *dollar (monetized)* is the currency-tagged
  cash sensitivity, never summed across currencies.
- **Scenario** — an explicit shocked market *state* (relative spot move, additive
  vol shift, time roll-down), never a greek multiplier.
- **Full reprice vs local approximation** — *full reprice* runs the shocked state
  back through C's pricer and is the only scenario PnL persisted; *local
  approximation* is the fast Taylor estimate from greeks, accurate only for small
  shocks.
- **Reconciliation breach** — a greek whose computed-vs-broker absolute difference
  exceeds the versioned threshold; an empty breach list means agreement.

### Integration and operations (Workstream E)

- **Actor** — the driver that transports market state into C/D's pure functions and
  stamps/persists their outputs; it holds no math. Under
  [ADR 0023](decisions/0023-nautilus-runtime-spine-and-library-leverage.md) Nautilus is the
  runtime spine, so the actor is a thin Nautilus `Actor` hosting those pure functions; the
  framework-free `backend/src/actor` is the salvage source for that pure core. The same actor
  runs live and replay (Nautilus's live==backtest property).
- **Same-code-path replay** — the invariant that a live run and a replay of the
  same trade date call the identical `run_analytics`, differing only in who
  populated the raw layer first. Verified by `test_replay_byte_identical.py`.
- **Valuation join** — the actor's math-free step that copies C's in-memory
  snapshot / forward / surface results into one `ContractValuationInput` per held
  contract for D to price. The only arithmetic is definitional (`k = ln(K/F)`,
  `vol = √(w/T)`).
- **Reconstruction (replay / backfill)** — `run_analytics` over a date range; the
  same compute path as live, never a second engine. A missing raw partition is
  flagged `MISSING`, never fabricated as an empty result.
- **Detection interval** — the bound within which an orchestration alert promises
  to notice a condition (e.g. collector death within `COLLECTOR_SILENCE_SECONDS`).
- **Escalation level** — the single signal a QC report collapses to for alerting:
  `page` (critical fail), `notice` (any other fail or warn), or `none` (clean).

### Test substrate

- **Rogues' gallery** — the named, immutable pathological fixtures (crossed quote,
  stale option, single-strike maturity, …) every workstream's edge-case tests bind to.
- **Known-answer fixture (oracle)** — a synthetic chain whose prices are generated
  from chosen true vol and SVI parameters, so the IV solver / forward engine /
  surface fitter are checked against an independently-derived answer, not their own
  output.

### Broker protocols and adapter layer (Workstreams M4 / M5)

- **Broker-seam direction ([ADR 0023](decisions/0023-nautilus-runtime-spine-and-library-leverage.md)):**
  Nautilus is the runtime spine; **IBKR rides Nautilus's adapter**, **Saxo/Deribit keep the
  `MarketDataAdapter` below** (Nautilus ships neither) — all three normalize to `RawMarketEvent` in
  the catalog the engine replays. The scalar pull `contracts.BrokerSession` is being retired;
  restore content-addressed event ids over the vendored running counter.
- **`BrokerTransport`** — the broker-agnostic Protocol for a live connection: sends subscription
  requests and delivers raw wire frames. Lives in `infra/`; never imported by `strategy`.
- **`MarketDataAdapter`** — the Protocol that normalizes a broker's wire frames into `BrokerTick`
  EAV rows. One implementation per broker, in its leaf package (`infra-<broker>`).
- **`BrokerTick`** — the normalized EAV row crossing the broker seam: `(provider, instrument_key,
  field, value, exchange_ts, receipt_ts)`. The only shape `infra` analytics ever consumes from a
  live source.
- **`FeedFault`** — a classified broker feed-health signal produced by `MarketDataAdapter` when the
  feed is degraded: pacing, entitlement, or unknown. Logged and counted; never written into the
  observation stream.
- **`ProviderFlow`** — a Protocol in `infra` implemented by each broker leaf: `open_session()`,
  `discover(...)`, `make_adapter(...)`, `resolve_config(...)`. Registered in the app layer (not in
  `infra/`, which never imports a leaf). ADR 0017.
- **`EventSource`** — a minimal Protocol for supplying raw events to the analytics pipeline:
  `events(provider, underlying, start, end) -> Iterable[RawMarketEvent]`. Makes live, replay, and
  future historical sources interchangeable without forking the pipeline. ADR 0016.

### Provider and exchange identity

- **Provider** — the data source leaf: `DERIBIT`, `SAXO`, `IBKR`. Identifies *who supplied the
  data*, not where it is listed. A first-class partition segment in all stores (ADR 0017).
- **Exchange** — the market listing venue: `DERIBIT`, `AMS`, `NASDAQ`, etc. Identifies *where the
  instrument is listed*. For crypto on Deribit, provider and exchange coincide; for equity they
  can differ (same option from Saxo or IBKR on the same Euronext listing).
- **`ProviderCapabilities`** — a frozen dataclass describing a broker leaf's capabilities:
  `asset_class`, supported underlyings, auth requirements, data latency, entitlement status.

### Deribit / crypto specifics

- **Mark IV** — Deribit's implied-volatility mark price for each option contract, published via
  the WebSocket tick stream. Used as input to the `mark_iv_divergence` QC check.
- **BTC / ETH underlying** — the two Deribit-listed underlyings in scope. Options are USD-settled;
  no native-coin accounting complexity.
- **Perpetual** — a crypto futures contract with no expiry; rolls continuously via a periodic
  **funding rate** (the fee paid by longs to shorts, or vice versa, to keep the perpetual price
  anchored to spot). Not an option; not in the surface fitting scope, but appears in Deribit market
  data and must be filtered before passing ticks to the IV/surface engine.
- **Funding rate** — the periodic cost of holding a perpetual position. Deribit publishes it as a
  tick field; it is a gap/metadata field for the purposes of the observation stream filter
  (`is_observation`).

### Saxo Bank / OAuth specifics

- **Access token (Saxo)** — OAuth2 bearer token valid for 20 minutes. Rotated automatically by
  `auth/token_manager.py` using the refresh token.
- **Refresh token (Saxo)** — OAuth2 token valid for 40 minutes, used to obtain new access tokens
  without re-authenticating.
- **`OptionsChain` endpoint** — the Saxo REST endpoint that returns a complete IV matrix and
  Greeks for a given underlying and expiry in a single call. Architecturally superior to IBKR's
  per-contract subscription for surface collection.

### Other domains

- **TODO: define** the `ecogest` domain terms (the `compt*` artifacts). Not yet
  covered; `ThomasOssen/` is personal scratch space outside the canonical structure.
