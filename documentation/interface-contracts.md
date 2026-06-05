# Interface contracts (frozen)

## The one rule

The typed dataclasses in `backend/src/contracts` are the only objects that cross a
workstream line. A workstream hands another workstream one of these, never a loose dict,
a tuple, or an internal in-memory type. That is the entire integration surface, and
freezing it is what let five workstreams build in parallel against each other without
constant renegotiation.

These contracts are owned by Workstream A. Nobody outside A edits a definition in place.
A needed change — a new field, a new table — is a request routed to A, because every
field ripples to four other workstreams. The contracts package says this itself
(`backend/src/contracts/__init__.py`): the registry internals are deliberately not
re-exported, so a consumer that finds itself reaching for them wants a new method on the
seam, routed through A, not a reassembly of A's internals.

## The frozen contracts

Twelve typed contracts, each living in one storage layer. "Produced by" is the
workstream that creates it; "consumed by" is who reads it across the line. The actor
(Workstream E) is the transport that moves C's and D's outputs into A's storage, so it
appears as a consumer-and-persister of most of them.

| contract | layer | produced by | consumed by |
|---|---|---|---|
| `InstrumentMaster` | raw (append-only) | B (universe resolution) | the actor, snapshots, the valuation join |
| `RawMarketEvent` | raw (append-only) | B (collectors) | the actor / replay, snapshots, lineage |
| `MarketStateSnapshot` | snapshot | C (snapshot builder) | forwards, IV, the valuation join, QC |
| `ForwardCurvePoint` | derived | C (forward estimator) | IV, surfaces, QC |
| `IvPoint` | derived | C (IV solver) | surfaces, QC |
| `SurfaceParameters` | derived | C (surface fit) | pricing, the valuation join, QC |
| `SurfaceGrid` | derived | C (surface fit) | reporting / inspection |
| `PricingResult` | derived | C/D (pricer) | risk, QC |
| `Position` | portfolio | the portfolio owner | the valuation join, risk |
| `RiskAggregate` | derived | D (risk core) | reporting, reconciliation, QC |
| `ScenarioResult` | derived | D (scenario engine) | reporting, QC |
| `QcResult` | qc | the QC plane (E) | the daily report, triage, alerting |

The exercise style is the one fact no contract carries — `InstrumentKey` has no style
field — so the actor injects it as a policy at the valuation join, defaulting to
European (`actor`, ADR 0006 decision 1). That is a deliberate seam, not a gap in the
contracts.

## What "frozen" does not mean

Frozen does not mean unchangeable; it means changed *only through A, only additively*.
The storage README (`backend/src/storage/README.md`, "Schema evolution and backfill
compatibility") is the authority on what an additive change is, and the rules are
enforced in code, not just documented:

1. A new field is added to the *end* of a contract and must be `Optional`. A partition
   written before the field existed reads back with it as `None` and the rest of the row
   intact. A *required* field that comes back absent is refused with
   `SchemaCompatibilityError` — it is never used to build an invalid instance.
2. No in-place removal or rename. A rename is a new nullable column plus a one-off
   backfill, not an edit to the existing one — because removing or renaming silently
   changes the meaning of historical partitions.
3. No in-place type change. The Arrow type of a column is fixed for the life of the
   table.
4. A primary-key change is a *new table*, not an evolution — the key set defines the
   partition and the dedup identity.
5. Nested bundles (the instrument key, the provenance stamp, diagnostic bundles) are
   stored as JSON columns and evolve as JSON: adding an optional field is
   backward-compatible; removing or renaming one follows rules 2–3.

Anything beyond rule 1 is a contract change, and a contract change is A-owned and routed
through A, never edited in place by a consumer. This is not a style preference — it is
what keeps months of historical partitions readable as the contracts grow, and it is the
crossing ADR 0007 (decision 3) called out explicitly when E added versioned partitions
to A's storage: that crossing was approved, recorded in the ADR, and claimed on the
board rather than made silently, precisely because editing A-owned storage is the
exception that proves the rule.

## Where to look

The contract definitions are in `backend/src/contracts/tables.py`; the table registry
(layer, append-only-ness, provenance requirement) is in
`backend/src/contracts/registry.py`; the public seam is `backend/src/contracts/__init__.py`.
The rationale for the seam and the storage-versioning crossing is in
`.agent/decisions/0006-risk-engine.md` and `.agent/decisions/0007-integration-ops.md`.

## Merge update (M0) — the frozen seam moves to `algotrading.infra.contracts`

The monorepo restructure (ADR 0018) ports this exact seam into the layered workspace as
**`algotrading.infra.contracts`** (`packages/infra/src/algotrading/infra/contracts/`).
The twelve typed contracts, the instrument key, the registry, and write-ahead validation
are unchanged in shape — only the import path changes (`from algotrading.infra.contracts
import …`) and `ProvenanceStamp` now comes from `algotrading.core`. M0 owns this package
in place of the old "Workstream A"; a change is still a request routed through M0.

M0 additionally **freezes two protocols** in the same package — the cross-package seams
the merge hinges on:

| protocol | module | role | implemented by | driven by |
|---|---|---|---|---|
| `StorageRepository` | `contracts/ports.py` | analytics data-plane port (raw + derived, versioned restatement, table-keyed) | M1 (Parquet/DuckDB) | every consumer reads/writes through it |
| `BrokerSession` | `contracts/broker.py` | broker-agnostic market-data seam (scalar `BrokerTick`, deterministic `content_event_id`) | **being reset — see note below** | Nautilus actor |

**Broker-seam update ([ADR 0023](../.agent/decisions/0023-nautilus-runtime-spine-and-library-leverage.md), 2026-06-05).**
Nautilus is the runtime spine. **IBKR** rides Nautilus's shipped adapter; **Saxo/Deribit** keep
their own `MarketDataAdapter` (Nautilus has neither) — all three normalize to `RawMarketEvent` in
the catalog the engine replays. The scalar pull `contracts.BrokerSession` above is on track to be
retired in favour of that catalog seam; C1 resolves the exact contract (ADR 0023, "Open"). The
`StorageRepository` and `RunRepository` ports are unaffected.

The blueprint's *other* store — the relational metadata/run registry — is a separate,
orthogonal port, `algotrading.infra.storage.ports.RunRepository` (M10; ADR 0015), not
part of the analytics seam above.
