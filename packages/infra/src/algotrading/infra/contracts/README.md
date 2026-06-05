# infra.contracts — the frozen seam

The typed data contracts and the two protocols every other workstream imports.
**M0 owns this package; nobody else edits it in place.** A needed change is a
request routed through M0, because every field ripples to the other workstreams.

## What lives here

- **Instrument identity** — `InstrumentKey` (the 9-field composite key from the
  blueprint, Part I), its canonical string form, and the three event timestamps
  (`exchange_ts`, `receipt_ts`, `canonical_ts`).
- **Table contracts** — the frozen dataclasses, one per table family from the
  blueprint data model (Part IV.C / Part IX data dictionary): `InstrumentMaster`,
  `RawMarketEvent`, `MarketStateSnapshot`, `ForwardCurvePoint`, `IvPoint`,
  `SurfaceParameters`, `SurfaceGrid`, `PricingResult`, `Position`, `RiskAggregate`,
  `ScenarioResult`, `QcResult`, `TriageRecord`. Each derived record carries a
  `ProvenanceStamp` (from `algotrading.core`) and a `source_snapshot_ts`.
- **Diagnostics bundles** — `ForwardDiagnostics`, `IvDiagnostics`, `SurfaceFitDiagnostics`.
- **Registry + validation** — `spec_for_table` / `table_for_contract` and
  `validate` / `validate_record` (write-ahead validation; rejects, never coerces).
- **The two frozen protocols:**
  - `StorageRepository` (`ports.py`) — the storage seam. Table-keyed read/write/list
    over the contract dataclasses, with the versioned-restatement semantics
    (`version=None` = live; `version=<V>` = one restatement; the two never mix; raw
    append-only tables refuse a versioned write). M1 implements it; everyone reads
    and writes through it.
  - `BrokerSession` (`broker.py`) — the broker-agnostic market-data seam. `BrokerTick`
    plus connect/subscribe/option-chain/ticks. M5's adapters satisfy it; M4's actor
    drives it. `content_event_id` gives a tick a deterministic, cross-process id.

## Rules

- Numbers are `float`/`int`, never decimal-strings. Timestamps are timezone-aware.
- The contracts are the *only* objects that cross a layer boundary (see
  `tasks/TESTING.md`). Consumers depend on a protocol, never on a concrete store or
  a broker SDK type.
