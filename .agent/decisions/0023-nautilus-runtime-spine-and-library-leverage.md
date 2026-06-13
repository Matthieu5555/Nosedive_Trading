# 0023 — Nautilus is the runtime spine; lean on proven libraries; keep Vincent's broker adapters

> **AMENDED 2026-06-13 (T-index-only-refactor).** Decision **3** below ("IBKR on Nautilus,
> **keep Vincent's Saxo/Deribit**") was **reversed**: Saxo and Deribit were removed entirely —
> IBKR is the sole live broker and the app is index-options-only (SX5E first, SPX parked).
> Everything else here (Nautilus is the runtime spine; lean on proven libraries; retire the
> hand-rolled `ib_async` session) **still stands**. Wherever this ADR says "keep / all three /
> Saxo+Deribit", read "IBKR only". See ADRs 0013/0014 (superseded) and
> `tasks/T-index-only-refactor.md`.

- **Status:** accepted by workspace-owner direction, 2026-06-05. **Supersedes
  [[0020-market-data-actor-wiring]]**; revises **[[0007-integration-ops]]** (decision 1,
  the "framework-free actor") and **[[0008-live-ibkr-adapter]]** (the hand-rolled `ib_async`
  session). **Resolves the [[0020-market-data-actor-wiring]] vs
  [[0022-m5-vendored-broker-slice]] contest** in favour of Vincent's vendored Saxo/Deribit
  slice.
- **Date:** 2026-06-05
- **Relates to:** [[0011-blueprint-as-plan-of-record]] (unaffected — the blueprint is
  framework-agnostic), [[0016-eventsource-seam-backtest-readiness]],
  [[0017-provider-dimension]], [[0019-one-immutable-raw-model]],
  [[0015-storage-repository-port-tiered-backends]].

## Context

Two independent builds of the same platform were merged toward the max-union (see
`tasks/TASKBOARD.md`). On the integration spine they diverged. Our side built a
**framework-free** actor and a thin scalar `contracts.BrokerSession` seam, and recorded a
standing decision *not* to depend on `nautilus_trader` (ADR 0007 d1, ADR 0020) — "Nautilus"
was kept only as a pattern name. Vincent's side built three working broker adapters (IBKR on
`ib_async`, Saxo/Deribit on `httpx`/`websockets`) behind a richer EAV
`MarketDataAdapter`/`BrokerTick`, vendored into `packages/infra` by M5 (ADR 0022, which
openly "contests 0020").

The workspace owner has now set the direction explicitly: **lean on well-built external
libraries as far as they go, and make Nautilus the actual runtime — not a borrowed pattern.**
This ADR records that reversal and the broker-seam resolution, so that no agent keeps building
the superseded "framework-free / delete-the-fork" plan that `TASKBOARD.md` and the old C1 spec
still described.

## Decision

**1. Library-leverage is the standing rule.** Prefer a proven, well-built library over
hand-rolled code wherever one exists — across the whole merge from here. The bar is *depth*:
the wrapper must hide real complexity (as our QuantLib / `py_vollib` / SciPy / DuckDB / PyArrow
wrappers already do), never a thin shim that only adds a dependency. When our code and Vincent's
both work, the one that rides a proven library wins unless it is less correct. Hand-write only
what no good library covers — the bespoke vol math (parity forward, IV diagnostics, SVI + no-arb,
the scenario grid) stays ours.

**2. Nautilus is the runtime spine** (reverses ADR 0020 / ADR 0007 d1). `nautilus_trader`
becomes a real dependency. Its data catalog, its backtest/replay engine, and its actor host are
the runtime. Our analytics stay pure functions (`snapshots → forwards → iv → surfaces → pricing
→ risk`); a thin Nautilus `Actor` drives them and stamps **our** `ProvenanceStamp` on the
outputs (Nautilus does not carry our provenance). Live and replay both run through Nautilus's
engine — its own live==backtest property now *is* the blueprint's single-code-path mandate
(Part IV §F, Step 13), so ADR 0007's no-dual-path invariant is **upheld**, just realized by
Nautilus instead of by hand.

**3. Broker adapters: IBKR on Nautilus, keep Vincent's Saxo/Deribit.** Use Nautilus's shipped
InteractiveBrokers adapter for IBKR (retiring the hand-rolled `ib_async` `IbkrBrokerSession` of
ADR 0008). Nautilus ships **nothing** for Saxo or Deribit, and Vincent's adapters work today —
so they are the **survivors**: kept, not deleted. This resolves the 0020-vs-0022 contest in
favour of the M5 vendored Saxo/Deribit slice. All three brokers normalize into the one catalog
the engine replays; no broker SDK type crosses into the analytics.

**4. The platform invariants are preserved and ride on top of Nautilus.** Content-addressed
event identity (restore `content_event_id` over the vendored running-counter `evt-{n}` —
glossary, `collectors/collector.py`), `ProvenanceStamp` on every derived record, the provider
dimension (ADR 0017), and the immutable raw layer (ADR 0019) all hold. The blueprint (ADR 0011)
is framework-agnostic and unchanged — Nautilus is one implementation choice under it, not a
revision of it.

## Open — for C1 to design and record (do not assume answers)

The direction above is decided; these integration details are **not**, and the docs say so
rather than inventing precision. C1 resolves each and records it (here or in a follow-on ADR):

- **Catalog topology.** Does Nautilus's `ParquetDataCatalog` *become* the immutable raw store
  (ADR 0019), or do we keep our DuckDB/Parquet raw layer and bridge it into Nautilus? Touches
  ADR 0015 / 0019 and `storage`.
- **The unifying broker seam.** With IBKR on Nautilus and Saxo/Deribit on `MarketDataAdapter`,
  the seam is most likely "everything normalizes to `RawMarketEvent` in the catalog," retiring
  the scalar pull `contracts.BrokerSession`. Confirm, and decide how Saxo/Deribit ticks reach
  the catalog.
- **Provenance bridging.** How a Nautilus event maps to our `RawMarketEvent` +
  `content_event_id` + `ProvenanceStamp`.
- **Salvage.** Lift the pure `run_analytics` core out of `backend/src/actor` into a Nautilus
  `Actor` subclass — the math-free driver logic is reused, the framework-free *hosting* is
  dropped.
- **Determinism proof.** Confirm Nautilus replay is byte-identical given injected
  `as_of`/`calc_ts` and no wall-clock/RNG leak — the headline replay test stays the gate.
- **Qlib / upper floors.** Still YAGNI (ADR 0016): design only the read-only seam now; Qlib
  re-enters at `strategy`/research when those layers land, not in this backbone.

## Consequences

The framework-free framing is retired everywhere agents read (TASKBOARD, the C1 spec,
`.agent/map.md`, `.agent/glossary.md`, `documentation/known-limitations.md`,
`documentation/interface-contracts.md`, `BIG_PICTURE.md`). C1 flips from "delete the fork, no
Nautilus" to "adopt Nautilus, keep Saxo/Deribit." The dependency surface grows
(`nautilus_trader` and its transitive deps; IBKR via Nautilus's extra) — accepted as the price
of the leverage. Exit cost is real but bounded: the analytics core is pure and
framework-independent by construction, so if Nautilus ever gets in the way the same functions
can be driven from a plain loop — the escape hatch ADR 0016's `EventSource` protocol exists to
keep open.
