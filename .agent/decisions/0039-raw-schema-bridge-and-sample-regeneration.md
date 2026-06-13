# 0039 — Raw-schema bridge: close the broker-raw ↔ contracts seam and make samples reproducible

> **AMENDED 2026-06-13 (index-only, [[0042-index-options-only-scope-ibkr-sole-broker]]).** The
> decision stands and is live; only a path label is dated — the committed samples now live under
> `packages/infra-ibkr/samples/` only (`infra-saxo` was removed; IBKR is the sole live broker). The
> raw-schema bridge and the broker-agnostic `RawMarketEvent` shape are unchanged.

- **Status:** accepted, 2026-06-10 (owner ruled OQ-A/OQ-B 2026-06-10). Closes the bridge
  deferral named in [[0021-analytics-core-merge]]. Lands **WS T-bridge**
  ([`../../tasks/T-bridge.md`](../../tasks/T-bridge.md)).
- **Date:** 2026-06-10.
- **Implements:** blueprint **Part XV data-governance** (immutable raw layer, single
  authoritative field definition) and **Part XVI test-matrix** ("a curated library of replay
  days … each replay day becomes a standard challenge set", `16-test-matrix.md`) under the
  blueprint-is-authority rule ([[0011-blueprint-as-plan-of-record]]). Adds no new raw model —
  it makes the **one** model ([[0019-one-immutable-raw-model]]) reachable from the committed
  sample format.
- **Relates to:** [[0019-one-immutable-raw-model]] (`contracts.RawMarketEvent` is the single
  canonical raw model; this ADR demotes `storage/events.py` to a wire-format, not a second
  store), [[0027-collection-seam-push-canonical]] (the push `RawCollector` is where broker
  ticks become canonical contracts events; the bridge is the *offline replay* counterpart of
  that same seam), [[0029-contract-field-names-conform-to-blueprint]] (the canonical field
  names the bridge maps **into**), [[0017-provider-dimension]] (`provider` is a first-class
  field dropped by the contracts schema — see the round-trip question below),
  [[0028-configuration-and-reproducibility-standard]] (a reproducible sample library is part
  of replay-a-past-day reproducibility).

## Context

Two `RawMarketEvent` classes coexist with no converter in `packages/infra`:

- **broker-raw EAV** — `storage/events.py:33` (`collector_session_id`, `field_value:
  Decimal|str|None`, `provider`, `contract_id_broker`, colon-delimited keys `OPT:ASML:…`).
  Serialized by `storage/json_io.py` (`events_to_json`/`events_from_json`). This is the format
  of the committed samples under `packages/infra-{ibkr,saxo}/samples/`.
- **contracts** — `contracts/tables.py:43` (`session_id`, `value: float`, `canonical_ts`,
  `trade_date`, pipe-delimited keys `SX5E|OPT|…`). This is what the canonical `ParquetStore`
  `raw_market_events` table persists ([[0019-one-immutable-raw-model]]).

The seam between them is **unbuilt and improvised**:

- `scripts/export_sample.py` computes a curated last-tick set then **refuses to write**
  (`return 2`, lines 85–91) — "serializing the store day through `events_to_json` is …
  not possible without a translation layer that does not exist in `packages/infra` today".
- The committed samples (`asml_real_2026-06-05.json`, `spy_real_2026-06-04.json`) are
  **hand-made fixtures** added in one convergence commit (`3a21d9f`); no code path regenerates
  them. The live IBKR collectors emit **only** the contracts schema; nothing writes broker-raw.
- The IBKR pipeline notebook (`notebooks/demo_pipeline_ibkr.ipynb`) carries an **ad-hoc copy**
  of the conversion (rename `collector_session_id→session_id`, `float(field_value)`, colon→pipe
  relabel, `canonical_ts` synthesis, sequence re-derivation) inline in `replay_sample`.

The blueprint mandate is the opposite of this: the regression library is built **from captured
raw days**, replayed through the *same* compute as production — not from hand-curated tables.
And the audit found this gap is the direct blocker for a committable **SX5E** sample (the
SX5E surface exists only as gitignored derived data; there is no raw sample to commit, and even
if the raw were captured, `export_sample` could not serialize it).

## Decision

**1. `contracts.RawMarketEvent` is the single canonical raw model; `storage/events.py` is a
named wire-format, never a second raw store.** This reaffirms [[0019-one-immutable-raw-model]]
explicitly (the audit read the surviving `storage/events.py` as a possible second raw layer —
it is not; nothing writes it to the raw Parquet layer). Its sole sanctioned role is the
on-disk **broker-raw sample** serialization for the committed fixture library.

**2. One bridge module — `universe/sample_bridge.py` — is the only place the two schemas
convert.** It exposes `events_to_contracts(broker_events, *, trade_date)` and
`contracts_to_events(contract_events, *, provider)`. It is the single home for: colon↔pipe key
relabel (via `universe.parse_instrument_key` ↔ `InstrumentKey.canonical`), `Decimal↔float`
value conversion, the `collector_session_id↔session_id` rename, `canonical_ts` derivation
(`exchange_ts or receipt_ts`, the live rule), and per-`(instrument, field)` **sequence
re-derivation** so `event_id = content_event_id(key, field, seq)` reproduces the live id. The
field-mapping table is pinned in the module docstring and covered by a round-trip test.

> **Placement note (corrected during T-bridge).** The bridge lives in `universe/`, not in
> `storage/` as first sketched: `universe` already imports `storage` (`universe/membership.py`
> reads `ParquetStore`), so a `storage` module importing `universe.parse_instrument_key` would
> create a **storage↔universe import cycle**. `universe` is the lowest layer that owns the
> colon-key vocabulary *and* already sits above both `storage` and `contracts` — the cycle-free
> home.

**3. The notebook and `export_sample.py` route through the bridge — no second copy.** The
inline conversion in `replay_sample` is deleted and replaced by `events_to_contracts(...)`;
`export_sample.py` calls `contracts_to_events(...)` then `events_to_json(...)` and **writes**.
This removes the duplicated-logic landmine the audit flagged.

**4. The sample library becomes reproducible from a stored raw day.** `export_sample.py
--symbol SX5E --date <d> --out packages/infra-ibkr/samples/sx5e_real_<d>.json` reads the
curated last-tick contracts set off the store, bridges it, and writes a committable broker-raw
sample — satisfying the blueprint's "replay day from captured raw" discipline. The existing
`reconstruct_sample.py` round-trip stays the determinism guard.

## Rulings (owner, 2026-06-10)

- **OQ-A — round-trip of dropped fields → synthesis-on-export.** The contracts schema drops
  `provider` and `contract_id_broker` ([[0017-provider-dimension]] keeps `provider` as a
  *partition*, not on the event). On export, `provider` is re-supplied as an argument and
  `contract_id_broker` is reconstructed from the run's `InstrumentMaster` set when present, else
  left null. The contracts raw model is **not** widened. A sample is a fixture, not the
  evidentiary record.
- **OQ-B — Decimal→float fidelity → diagnostic, accepted.** A sample exported *from* a stored
  contracts day is float-origin, so its `Decimal` is `Decimal(str(value))` — exact to the stored
  precision, not to the broker's original Decimal (already lost at capture). The bridge records a
  one-line diagnostic on export; the loss is a property of the capture boundary, not the bridge,
  and is acceptable for market-data magnitudes.

## Consequences

- The committable **SX5E sample** is unblocked the moment SX5E raw is landed
  ([[0040-ingestion-persistence-invariants]] guarantees that landing); the notebook's SX5E
  nappe can then replay a committed sample like ASML instead of reading gitignored
  `data/derived`.
- The notebook's hand-rolled conversion is deleted (one fewer "bricolage" copy); a single
  tested bridge owns the seam.
- The [[0021-analytics-core-merge]] bridge deferral is **closed**, not carried.
- New code is additive: a new `universe/sample_bridge.py`, a rewritten `export_sample.py`, a
  notebook edit, and bridge round-trip tests. No change to the canonical raw model or to any
  persisted schema. (Placement is `universe/`, not `storage/`, to avoid a storage↔universe import
  cycle — see the placement note above.)
