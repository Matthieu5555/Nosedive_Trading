# 0027 — Collection seam: the push `RawCollector` is canonical; harvest the pull seam, then retire it

> **AMENDED 2026-06-13 (T-index-only-refactor).** The adapter inventory below ("all three live
> adapters … Deribit … Saxo") is dated — Saxo/Deribit were removed; the surviving push adapters
> are IBKR-TWS and IBKR-CP-REST. The push-`RawCollector`-is-canonical decision is unaffected. See
> ADR 0023's amendment.

- **Status:** accepted, 2026-06-05; **implemented by C6, 2026-06-05** — one `BrokerTick`, one
  `RawCollector`, content-addressed live capture, the pull seam deleted, and the four
  collection-coupled use-cases (`collect_live`, `surface_job`, the handover smoke stage,
  `provider_flow`) ported onto it. See `tasks/C6-collection-seam-unification.md`. Formalizes the
  seam direction already set by
  [[0023-nautilus-runtime-spine-and-library-leverage]] ("the EAV `BrokerTick` +
  `MarketDataAdapter` is the survivor; restore content-addressed ids; retire the unused pull
  seam") and **resolves the collection-seam fork deferred in
  [[0026-orchestration-observability-reconciliation]] §4**.
- **Date:** 2026-06-05
- **Relates to:** [[0019-one-immutable-raw-model]] (the EAV `RawMarketEvent` shape this writes),
  [[0020-market-data-actor-wiring]] (raw-layer replay, live==replay — the seam this finally
  reconciles), [[0003-market-data-plane]] (content-addressed idempotency this restores),
  [[0007-integration-ops]] (no-dual-path invariant — the reason the duplication must collapse),
  [[0017-provider-dimension]] (the `provider` field the push tick already carries).

## Context

The merge grew **two** collection seams for the one job of turning a broker market-data
session into the immutable `RawMarketEvent` raw layer. Both currently live in
`packages/infra`, each with its own `BrokerTick`:

- **PULL** — `contracts.broker.BrokerSession` + `connectivity.SessionSupervisor`. The
  consumer *pulls*: `session.ticks()` yields. Its `BrokerTick` is
  `(broker_contract_id, field_name, value: float, sequence, exchange_ts)` — value is float
  only, but it carries a **`sequence`** (the stable per-session ordinal that makes the event
  id content-addressed and writes idempotent, ADR 0003). `SessionSupervisor` is the
  single home for reconnect/backoff, client-id bands, and loss-aware `GapInterval` recording.
- **PUSH** — `collectors.RawCollector` + `collectors.normalize.BrokerTick` +
  `MarketDataAdapter`. The adapter *pushes*: `set_tick_callback(...)`. Its `BrokerTick` is
  `(instrument_key, field_name, value: float|str|None, underlying, provider, exchange_ts,
  contract_id_broker)` — it carries the **`provider`** dimension (ADR 0017) and categorical
  values, but **no `sequence`**. `RawCollector` normalizes each tick into `RawMarketEvent`,
  batches Parquet writes, and counts `FeedFault`s (pacing/entitlement) into the session
  summary. Reconnect/heartbeat live in the session beneath it.

Three facts decide it. **(1)** The blueprint's reference collector (Part IV) is literally
push — `RawCollector.on_event(broker_event)` → `set_callback` → `heartbeat_loop` — over the
flat EAV raw shape (Part IV schema rules; ADR 0019). **(2)** The code already voted: all
three live adapters (Deribit, IBKR-TWS, IBKR-CP-REST, Saxo) are written on the push
`MarketDataAdapter`/`RawCollector`; only `deribit_transport.py` still touches the pull seam.
ADR 0023 already named the push EAV tick "the survivor." **(3)** The course transcripts say nothing about ingestion transport; they set no override
(`documentation/transcripts/` was removed; course pedagogy lives in `ThomasHossen/MM_options_trading.md`).
The blueprint, the plan of record (ADR 0011; absorbed into `TARGET.md`), is the binding authority,
and it is push.

Keeping both is the live violation of the no-dual-path invariant (ADR 0007): two
`BrokerTick`s, two session protocols, two supervision homes, drifting. It is exactly why the
four collection-coupled use-cases (`collect_live`, `surface_job`, the handover
connectivity-smoke stage, `provider_flow`) cannot be ported and `backend/` cannot be fully
retired.

## Decision

**1. The push `RawCollector` + `collectors` seam is the one canonical collection path.**
A broker adapter is a `MarketDataAdapter` (push: `subscribe` / `set_tick_callback` /
`set_fault_callback` / `unsubscribe_all`). The collector normalizes/stamps/persists into the
append-only `RawMarketEvent` layer; nothing downstream is touched. This is the blueprint's
literal design and the shape all live adapters already speak.

**2. Harvest two things from the pull seam before retiring it — they are objectively better
and the push seam lacks them:**
- **`sequence` → content-addressed idempotent `event_id`.** The push `BrokerTick` gains a
  `sequence` (stable per-session ordinal); the normalizer derives `event_id =
  content_event_id(instrument_key, field_name, sequence)` (ADR 0003), so re-delivery and
  kill-and-restart write each event exactly once. This restores what ADR 0023 called for
  ("restore content-addressed ids").
- **`SessionSupervisor`** (backoff, client-id bands, loss-aware `GapInterval`) is kept as the
  **session-management layer beneath the adapter** — the blueprint's "reconnect logic in
  exactly one place" (Part I, connectivity service). It manages the session; it no longer
  defines the tick type or the consumer's pull loop.

**3. One `BrokerTick`.** The two definitions collapse into a single type: the push shape
(`provider`, `underlying`, categorical `value`) **plus** `sequence`. The pull
`contracts.broker.BrokerTick` and the iterator-pull `BrokerSession.ticks()` consumer contract
are retired once nothing imports them.

**4. Replay stays raw-layer replay (ADR 0020 option b), through the same collector.** The
replay source re-emits stored `RawMarketEvent` as the same unified `BrokerTick` into the same
`RawCollector`. Live and replay differ only in the source of events — never a second code
path. ADR 0020 was superseded by ADR 0023 on *which* seam; this ADR settles it on the push
seam and keeps 0020's live==replay guarantee intact.

## Alternatives considered

- **Keep the pull `BrokerSession`/`SessionSupervisor.ticks()` as the canonical seam.**
  Rejected: it contradicts the blueprint's push pseudocode, it is *not* what the live
  adapters were built against (all four are push), and its tick lacks the `provider`
  dimension (ADR 0017) the multi-broker plane needs. Its two genuinely good parts (`sequence`
  idempotency, `SessionSupervisor`) are harvested rather than lost (§2).
- **Keep both, bridged by an adapter shim.** Rejected as the dual-path the whole merge exists
  to remove (ADR 0007): "dual code paths always drift" (blueprint Step 13). The push/pull
  *control-flow* impedance is absorbed **inside each adapter** (a callback-native SDK feeds the
  callback sink; a poll-native source wraps the same), which is precisely the adapter's job
  ("transform broker callbacks into normalized internal events", blueprint Part IV) — not a
  reason for two seams above it.

## Consequences

- The four deferred use-cases (`collect_live`, `surface_job`, the handover smoke stage,
  `provider_flow`) become portable onto one seam — see `tasks/C6-collection-seam-unification.md`.
- Saxo/Deribit wire onto the Nautilus runtime through the unified collector (the `transport`
  switch consumer).
- The pull seam (`contracts.broker.BrokerTick`, pull-side `BrokerSession.ticks()`,
  `deribit_transport.py`'s pull path) is retired once unreferenced; `backend/`'s collection
  modules retire behind it (C5).
- Idempotency is restored to the live path: the EAV write is content-addressed again, so the
  byte-identical replay/provenance guarantees hold for live capture, not only replay.
- `SessionSupervisor` keeps its single-home reconnect role beneath the adapter; nothing else
  owns reconnect.
