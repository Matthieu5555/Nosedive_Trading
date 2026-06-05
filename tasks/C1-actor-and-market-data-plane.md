# C1 — Land the market-data plane + actor spine in `packages/infra`, on Nautilus

> **Direction reset by [ADR 0023](../.agent/decisions/0023-nautilus-runtime-spine-and-library-leverage.md) (2026-06-05).**
> This spec replaces the earlier "framework-free actor, no Nautilus, delete the M5 fork,
> retarget all three leaves onto the scalar `contracts.BrokerSession`" plan. The new direction:
> **Nautilus is the runtime spine; IBKR rides Nautilus's adapter; Vincent's Saxo/Deribit
> adapters are kept (survivors).** Read ADR 0023 before starting.

- **Owns:** `packages/infra/src/algotrading/infra/{connectivity,collectors,universe,actor}/**` (+ READMEs), `packages/infra-{ibkr,saxo,deribit}/**`, and a follow-on ADR recording the integration design ADR 0023 left open.
- **Depends on:** M0 (`contracts`, the raw-event model), M1 (storage). `nautilus_trader` is added as a real dependency. M0/M1 landed.
- **Blocks:** C2 (qc reads actor outputs — landed, waiting on the wiring), C3 (orchestration drives the actor; the headline tests need it), C5 (retiring `backend/{actor,connectivity,collectors,universe}`).

## Decide first (ADR 0023 "Open") — record the answers before coding

> **RESOLVED 2026-06-05.** The three "decide first" items below are answered:
> **(1) catalog topology** — our `RawMarketEvent` + `ParquetStore` stays the system of record,
> Nautilus bridges ([ADR 0025](../.agent/decisions/0025-nautilus-host-catalog-topology.md));
> **(2) unifying broker seam** — the push `RawCollector` is canonical, the scalar pull
> `BrokerSession` retired ([ADR 0027](../.agent/decisions/0027-collection-seam-push-canonical.md)),
> and the `BrokerTick`/`RawMarketEvent` reconciliation + content-addressed-id restore (item 4 in
> "What to do") **moved to [C6](C6-collection-seam-unification.md)** — they are no longer C1's;
> **(3) provenance bridging** — recorded in ADR 0025 §3. Kept below as the original framing.

Not pre-decided; resolve each and write it into the follow-on ADR:

1. **Catalog topology.** Does Nautilus's `ParquetDataCatalog` *become* the immutable raw store (ADR 0019), or do we keep the DuckDB/Parquet raw layer and bridge it into Nautilus? (Touches ADR 0015/0019 and `storage`.)
2. **Unifying broker seam.** Confirm the seam is "everything normalizes to `RawMarketEvent` in the catalog," and retire the scalar pull `contracts.BrokerSession`. Decide how Saxo/Deribit `MarketDataAdapter` output reaches the catalog.
3. **Provenance bridging.** How a Nautilus event maps to our `RawMarketEvent` + `content_event_id` + `ProvenanceStamp`.

## Objective

One market-data plane and one actor spine in `packages/infra`, on the Nautilus runtime. The actor holds no math: it is a thin Nautilus `Actor` that transports market state into M2/M3's pure functions, stamps their outputs with our provenance, and persists through M1. **The same actor runs live and replay** — now via Nautilus's own live==backtest engine — the load-bearing invariant the whole merge rests on.

## What to do

1. **Add Nautilus as the runtime.** `nautilus_trader` is a real dependency. Stand up its data catalog, its replay/backtest engine, and an `Actor` host. The actor subclass hosts the pure `run_analytics` core salvaged from `backend/src/actor` — the math-free driver logic survives; the framework-free *hosting* is dropped.
2. **IBKR on Nautilus.** Use Nautilus's shipped InteractiveBrokers adapter for IBKR connectivity + market data, feeding the catalog. Retire the hand-rolled `ib_async` `IbkrBrokerSession` (ADR 0008) once parity is proven.
3. **Keep Vincent's Saxo/Deribit adapters.** Nautilus ships neither. The vendored `infra-saxo`/`infra-deribit` `MarketDataAdapter` slices are survivors: keep them, and feed their normalized ticks into the same catalog the engine replays (per the topology decided above). Fix the 3 red Saxo-config tests.
4. **One normalized event, content-addressed.** All three brokers normalize to one `RawMarketEvent` shape with **content-addressed event ids** (`content_event_id` over `(instrument_key, field, sequence)`) — restore this over the vendored running-counter `evt-{n}` so reconnect re-delivery and restart dedup to exactly one write. Reconcile the two `BrokerTick`/`RawMarketEvent` models into one (ADR 0022's "promote the richer EAV model" path, keeping the scalar model's idempotent identity).
5. **Keep chain-selection one policy.** Discovery emits a broker-neutral chain menu; one `chain_planning` policy picks what to qualify and stream. The adapter supplies the menu, never invents selection.
6. **Record the resolution** in the follow-on ADR (the open calls above + what was built).

## Frozen / preserved invariants

Determinism (injected `as_of`/`calc_ts`, no wall-clock/RNG), the immutable raw layer (ADR 0019), provenance on every derived record, the provider dimension (ADR 0017), and one code path for live and replay (now Nautilus's engine). No broker SDK type — including Nautilus's own — crosses into the analytics; the actor builds normalized state itself inside `run_analytics`.

## Test surface (in `packages/infra/tests` + per-leaf tests)

Read `tasks/TESTING.md` first.
- **Kill-and-restart idempotency:** a killed collector restarts with no duplicated and no corrupted raw events (content-addressed ids make this hold).
- **Deterministic universe dedup** across processes.
- **Actor byte-identical, live vs replay:** drive the actor once from a live Nautilus feed and once from the same events replayed off the catalog → byte-identical derived outputs.
- **Broker-agnostic normalization:** IBKR (via Nautilus), Saxo, and Deribit ticks normalize to one indistinguishable `RawMarketEvent` shape.
- **Per-leaf:** each adapter maps its native tick fields to the string `field_name` inside the adapter; no SDK type leaks above the boundary.

## Done criteria

One market-data plane and the actor as the sole driver, both in `packages/infra` on Nautilus; IBKR on Nautilus's adapter; Saxo/Deribit kept and feeding the catalog; one content-addressed `RawMarketEvent`; the follow-on ADR written; **root gate green (the 3 Saxo failures cleared)**, `nautilus_trader` in the gate. `backend/{actor,connectivity,collectors,universe}` are now stale dupes → C5.

## Gotchas

One spine — the actor — no second driver and no "historical-only" path, or the byte-identical guarantee becomes a lie. The actor stamps but computes nothing; any math in it belongs in M2/M3. Do not let Nautilus's types leak past the adapter/normalization boundary into the analytics — the pure functions stay framework-independent (the ADR 0016 escape hatch, and what keeps them testable on their own). Restore content-addressed ids before relying on idempotency.
