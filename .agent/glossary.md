# Glossary — domain vocabulary

Project-specific terms where guessing a wrong synonym would silently corrupt code.
One name per concept; if you reach for a synonym, use the term here instead. Add a
term the moment you catch an agent (or human) misreading it. Generic quant/vol
vocabulary (look-ahead, point-in-time, total variance, log-moneyness, SVI, no-arb)
lives in `TARGET.md` — not duplicated here.

## Identity and provenance

- **Instrument key (canonical key)** — the nine-field economic identity
  (`contracts.InstrumentKey`) collapsed by `canonical()` into a deterministic
  pipe-joined string; built by hand, **never** a salted `hash()`, so it is
  byte-identical across machines. The primary key derived records join on.
- **Broker contract id (`conId`)** — the broker's external contract id; one of the
  nine `InstrumentKey` fields, never the platform's sole identifier. Recoverable via
  `broker_contract_id_from_canonical`.
- **Content-addressed event id** — a raw event's identity, SHA-256 of
  `(instrument_key, field, sequence)`; re-delivery or restart dedups to one write.
- **Provenance stamp** — the immutable `ProvenanceStamp` on every derived record:
  source refs, source timestamps, calc time, code version, config hashes, content hash.
- **Source record ref** — a typed `SourceRecordRef` pointing to one source row by
  table + *full* canonical primary key, so lineage resolves to exactly one row.
- **Config hash** — SHA-256 of a config bundle's canonical JSON, stamped onto derived
  records (per-bundle `config_hashes`, not one global hash). Environment settings stay out.
- **Config bundle** — one of the six YAML files under `configs/`; the unit of config
  authoring, validation, and hashing. The four economic bundles hash independently (C7).
- **Table family / contract** — one of the twelve frozen `contracts.tables`
  dataclasses that may cross a workstream seam; metadata lives in the registry `TableSpec`.

## Storage

- **Append-only / immutable raw layer** — the `raw` layer, where an existing primary
  key may never be overwritten; ticks are written once and never edited.
- **Versioned partition (restatement)** — a `version=<V>` sub-partition holding a
  derived analytic recomputed under newer code, landing *beside* the live (unversioned)
  partition. A version-blind read (`version=None`) returns live rows only (ADR 0007).
- **Run-state ledger** — the append-only JSON-lines record of which end-of-day stage
  finished cleanly for which trade date; basis for idempotent restart and dashboard health.

## Market data and collection

- **Gap event (meta-event)** — a raw event under a reserved `__`-prefixed field
  (e.g. `__gap__`) recording an *absence* of data; downstream filters it via `is_observation`.
- **Session id** — the collector's idempotency scope, stable across restarts (typically
  derived from the trade date) so a restart recognizes already-written events.
- **Push collection seam / `RawCollector`** — the push-canonical boundary where a broker feed
  *pushes* events through a `MarketDataAdapter` into the broker-agnostic `RawCollector`,
  normalized into the append-only `RawMarketEvent` table. (The old pull model is retired.)

## Analytics (Workstream C)

- **Reference spot** — the single labeled price for an instrument at a snapshot, by the
  fixed ladder mid → last → close → carry_forward; the chosen rung is in `reference_type`.
- **As-of read (inclusive boundary)** — a point-in-time read where `canonical_ts <=
  snapshot_ts` is usable and strictly-later events are dropped as the future; ties broken
  by `event_id`. The platform's look-ahead boundary.
- **Parity forward** — the forward `F` recovered from put-call parity
  `C − P = DF·(F − K)` read across strikes, recovering `F` and `DF` jointly.

## Risk (Workstream D)

- **Line vs lot** — a *lot* is one `Position` row for a contract from a single source;
  a *line* is one `PositionRisk` row after all lots of that contract net. The line *is*
  the contract.
- **Per-unit / position-level / dollar greek** — three scalings of one sensitivity:
  *per-unit* straight from the pricer; *position-level* is `per_unit × multiplier ×
  quantity`; *dollar* is the currency-tagged cash sensitivity, never summed across currencies.
- **Scenario** — an explicit shocked market *state* (relative spot move, additive vol
  shift, time roll-down), never a greek multiplier.
- **Full reprice vs local approximation** — *full reprice* runs the shocked state through
  C's pricer and is the only scenario PnL persisted; *local approximation* is the fast
  Taylor estimate from greeks, accurate only for small shocks.
- **Reconciliation breach** — a greek whose computed-vs-broker absolute difference exceeds
  the versioned threshold; an empty breach list means agreement.

## Integration and operations (Workstream E)

- **Actor** — the driver that transports market state into C/D's pure functions and
  stamps/persists their outputs; it holds no math. A thin Nautilus `Actor` in
  `packages/infra/.../infra/actor`. Same actor runs live and replay.
- **Same-code-path replay** — the invariant that a live run and a replay of the same trade
  date call the identical `run_analytics`, differing only in who populated raw first.
- **Valuation join** — the actor's math-free step copying C's snapshot/forward/surface
  results into one `ContractValuationInput` per held contract for D to price.
- **Reconstruction (replay / backfill)** — `run_analytics` over a date range; same compute
  path as live. A missing raw partition is flagged `MISSING`, never fabricated as empty.
- **Escalation level** — the single signal a QC report collapses to for alerting: `page`
  (critical fail), `notice` (other fail/warn), or `none` (clean).

## Broker seam and identity

- **`MarketDataAdapter`** — the Protocol normalizing a broker's wire frames into `BrokerTick`
  EAV rows. One implementation per broker, in its leaf package (`infra-<broker>`).
- **`BrokerTick`** — the normalized EAV row crossing the broker seam: `(provider,
  instrument_key, field, value, exchange_ts, receipt_ts)`. The only live-source shape `infra` consumes.
- **`FeedFault`** — a classified broker feed-health signal (pacing / entitlement / unknown);
  logged and counted, **never** written into the observation stream.
- **`ProviderFlow`** — a Protocol implemented by each broker leaf (`open_session`,
  `discover`, `make_adapter`, `resolve_config`), registered in the app layer (ADR 0017).
  IBKR is the sole live broker; the seam stays generic so another could rejoin.
- **Provider vs exchange** — *provider* is the data-source leaf that supplied the data
  (today only `IBKR`); *exchange* is the listing venue (`EUREX`, `CBOE`, `XPAR`). They differ —
  provider `IBKR` with exchange `EUREX` for SX5E. Both are first-class partition segments.

## Test substrate

- **Rogues' gallery** — the named immutable pathological fixtures (crossed quote, stale
  option, single-strike maturity, …) every workstream's edge-case tests bind to.
- **Known-answer fixture (oracle)** — a synthetic chain priced from chosen true vol + SVI
  params, so the IV solver / forward engine / surface fitter check against an
  independently-derived answer, not their own output.
