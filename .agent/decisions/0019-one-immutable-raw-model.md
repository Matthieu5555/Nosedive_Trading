# 0019 — One immutable raw model: flat-EAV RawMarketEvent over Parquet (M1)

- **Status:** accepted
- **Date:** 2026-06-05
- **Scope:** M1 — the storage merge. Resolves the one reconciliation the M1 spec flags.
- **Relates to:** [[0015-storage-repository-port-tiered-backends]], [[0018-monorepo-keystone-m0]],
  [[0017-provider-dimension]].

## Context

The two builds modelled the raw layer differently and the M1 spec demands the choice be
made once, explicitly: do not let two raw representations coexist.

- **Ours:** an immutable, append-only `RawMarketEvent` written to versioned-partition
  Parquet, with the versioned-read semantics we fixed (`version=None` = live only,
  `version=<V>` = one restatement, the two never mix; append-only tables refuse a
  versioned write). The versioning is a real double-count *bug fix* on the
  reconstruct-beside-live path, not a preference.
- **Vincent's:** an explicit EAV raw-event model (`events.py`) plus tiered/derived stores.

These are not actually two different *models*. Our `RawMarketEvent` is already an EAV
row: the **entity** is `instrument_key`, the **attribute** is `field_name`, the **value**
is `value`, carried with the three blueprint timestamps (`exchange_ts`, `receipt_ts`,
`canonical_ts`) and the `(session_id, event_id)` key. It is EAV expressed in a flat,
explicit columnar schema — exactly what the blueprint asks for (Part IV / Step 4: "use
simple, explicit schemas; avoid storing nested structures unless they materially reduce
complexity; favor readability and debuggability over compact cleverness").

## Decision

**One immutable raw representation: the flat-EAV `RawMarketEvent` contract, written to
append-only Parquet partitions.** The repository port (`StorageRepository`, frozen by M0)
exposes an append-only raw write and a versioned derived write; the raw layer is the one
immutable source of truth, and every derived analytic is recomputable from it.

- Raw stays **Parquet**, not a row-store: `decimal`/float values round-trip exactly and
  columnar append-only scan-by-date is the right shape; a mutable row-store would break
  byte-identical replay (consistent with [[0015-storage-repository-port-tiered-backends]],
  which keeps raw on Parquet as the replay anchor).
- Our **versioning semantics are carried in whole** — they are the bug fix, so they win:
  `version=None` reads live/unversioned rows only, `version=<V>` reads exactly that
  restatement, the two never mix, and a versioned write to an append-only table raises
  `VersionedWriteNotAllowed`.
- Vincent's separate EAV `events.py` is **not** carried as a second raw layer; its idea
  (entity/attribute/value) is already realised by `RawMarketEvent`. His derived/tiered
  structure is adopted at the *port and metadata-tier* level (decomposed `RunRepository`
  in `storage.ports`, ADR 0015), not as a parallel raw store.

## Implementation (this change)

Ported the proven flat-tree storage layer into `algotrading.infra.storage`:
`ParquetStore` (`adapter.py`) + `partitioning.py` + `schema.py` + `serialization.py` +
`errors.py`, with imports rebound to `algotrading.core` and
`algotrading.infra.contracts`. `ParquetStore` satisfies the frozen `StorageRepository`
port structurally. It coexists with the M10 metadata tier (run registry) already in this
package; `storage/__init__` now exports both tiers.

## Test surface (M1-specific, all green)

`packages/infra/tests/test_storage.py` — self-contained (the flat fixture library is
still entangled with the risk workstream, so it is not imported here):

- Port conformance: `ParquetStore` satisfies `StorageRepository`.
- Versioning invariant: a restatement coexists with live; a version-blind read returns
  live rows only; recomputing a derived partition leaves the raw partition byte-unchanged;
  deleting one version leaves the others; an invalid version segment is refused.
- Append-only: overwriting an existing observation and a versioned write to a raw table
  are both refused; distinct observations and duplicate-in-batch are handled.
- Golden bytes: writing the same records yields byte-identical Parquet (the stable
  substrate M7's byte-identical replay needs).
- Schema evolution on read: an absent optional column reads as `None`; an absent required
  column raises `SchemaCompatibilityError`.
- Lineage: `raw_events_for` resolves by full primary key and does not conflate the same
  `event_id` across two sessions.

## Consequences

One raw layer, recomputable downstream, with our versioning bug fix preserved under the
merged package. M4 can capture raw and replay from it; M7 can persist runs/derived through
the port. The DuckDB derived-query tier (ADR 0015 §2) stays deferred until a concrete
consumer exists; `ParquetStore.read` already uses DuckDB-over-Parquet for reads, so the
retrofit is additive. Follow-up (not M1): port the shared fixture library once the risk
workstream's coupling to it is resolved, so storage tests can reference named fixtures.
