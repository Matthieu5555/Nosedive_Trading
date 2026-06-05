# 0024 — Hosting the analytics in Nautilus: our raw layer stays the system of record

- **Status:** accepted, 2026-06-05. Implements the C1 design questions left **open** by
  [[0023-nautilus-runtime-spine-and-library-leverage]] (§"Open — for C1 to design and record").
- **Date:** 2026-06-05
- **Relates to:** [[0019-one-immutable-raw-model]] (upheld), [[0016-eventsource-seam-backtest-readiness]]
  (still YAGNI — see below), [[0007-integration-ops]] (no-dual-path invariant, now realized by
  Nautilus), [[0017-provider-dimension]], [[0015-storage-repository-port-tiered-backends]].

## Context

ADR 0023 made `nautilus_trader` the runtime spine and reserved the integration details for C1
to "design and record (do not assume answers)". This ADR records those decisions for the
**IBKR-first** slice (Saxo/Deribit are on standby; their move onto the runtime is a later task).

## Decision

**1. Catalog topology — our raw layer is the system of record; Nautilus bridges.**
The immutable `RawMarketEvent` + `ParquetStore`/`StorageRepository` (ADR 0019) remains the one
source of truth: content-addressed `event_id` idempotency, the three timestamps, append-only
partitions, and `ProvenanceStamp` lineage all keep living there. Nautilus is the runtime/replay
engine and (for IBKR) the transport; its `ParquetDataCatalog` is, at most, the engine's working
store, **never** the system of record. Events cross the boundary one way at the seam: Nautilus
data ↔ `RawMarketEvent`, normalized in `infra/actor/nautilus_host.py`. We did **not** adopt the
Nautilus catalog as the raw store — doing so would re-implement ADR 0019's guarantees on a store
that does not offer them and would put the byte-identical/provenance gate at risk.

**2. The actor host wraps the unchanged pure analytics.** `AnalyticsActor` (a thin
`nautilus_trader.common.actor.Actor`) accumulates the replayed `RawMarketEvent` stream and, on
engine stop, calls the **unmodified** pure `run_analytics` (`actor/driver.py`) over injected
`as_of`/`calc_ts`/`config`/`config_hash`, then persists via the existing `persist_outputs`. The
math is reused verbatim; only *who drives the events* changed. `driver.py` stays
`nautilus_trader`-free (the framework import lives only in `nautilus_host.py`), so the analytics
remain framework-independent and the ADR 0016 escape hatch (drive the same functions from a plain
loop) stays open in fact, not just in principle.

**3. Provenance bridging.** Each `RawMarketEvent` is carried as a Nautilus custom data point
(`RawMarketEventData`, a `@customdataclass`) whose `ts_event`/`ts_init` is the event's
`canonical_ts` in nanoseconds, so the engine's simulated clock advances on our canonical time.
The bridge is **lossless** (integer-microsecond timestamp round-trip; scalars carried verbatim),
asserted on its own in the determinism test. `content_event_id` and `ProvenanceStamp` are
untouched — they are produced exactly as before by the raw layer and `run_analytics`.

**4. Determinism is the gate.** `tests/test_nautilus_replay_byte_identical.py` proves that
hosting `run_analytics` in the Nautilus engine returns the same `ActorOutputs` (stamps included)
and writes byte-for-byte identical Parquet as a direct call, on a multi-underlying day. This is
the same single-code-path guarantee the flat build proved for live-vs-disk-replay, now proven for
direct-call-vs-Nautilus-engine. The engine runs on its simulated clock with injected
`as_of`/`calc_ts`; no wall-clock or RNG leaks in.

**5. `EventSource` (ADR 0016) stays YAGNI for now.** The unifying seam is not implemented in this
slice: the host takes an event `Sequence` directly, and `run_analytics` is the single path
regardless of whether the events came from disk replay or the engine — so the no-historical-fork
invariant holds without the protocol. The `EventSource` protocol re-enters when a *polymorphic*
consumer needs it (a live IBKR feed and disk replay selected at runtime), not before; adding it
now would be speculative abstraction.

## Consequences

- `nautilus_trader` is a hard dependency of `algotrading-infra`; it caps pandas at `<3`, so the
  (unused — no source imports pandas) `pandas` pin was relaxed. The IBKR live path rides Nautilus's
  `[ib]` extra (`nautilus-ibapi`), an optional install absent from the gate.
- IBKR's hand-rolled `ib_async` `IbkrBrokerSession` (ADR 0008) is superseded by Nautilus's shipped
  InteractiveBrokers adapter; the live wiring lands to the verifiable boundary (no TWS Gateway in
  CI). Saxo/Deribit and their vendored capture slice are untouched (ADR 0023 keeps them).
- Exit cost stays bounded: the analytics core is pure and framework-independent, so if Nautilus
  ever gets in the way the same functions run from a plain loop — the property decision 2 protects.
