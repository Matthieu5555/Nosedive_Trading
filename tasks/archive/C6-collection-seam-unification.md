# C6 — Collection-seam unification + port the four collection-coupled use-cases

- **Owns:** collapsing the two collection seams into one (per
  [ADR 0027](../../.agent/decisions/0027-collection-seam-push-canonical.md)), then porting the
  four use-cases that ADR 0026 §4 left deferred, and wiring Saxo/Deribit onto the runtime.
- **Depends on:** ADR 0027 (accepted). The push `RawCollector` seam, the pull
  `SessionSupervisor`/`contracts.broker.BrokerTick`, and all four live adapters already exist
  in `packages/infra` + `packages/infra-{ibkr,saxo,deribit}`.
- **Blocks:** the *completion* of [C5](C5-retire-backend.md) — `backend/`'s collection +
  orchestration modules cannot fully retire until these use-cases live in `packages/`. Also
  feeds [C4](C4-frontend.md)'s run/health routers (the live path).
- **State going in:** two `BrokerTick`s, two session protocols, two supervision homes coexist
  — the dual-path the merge exists to remove (ADR 0007). Four use-cases (`collect_live`,
  `surface_job`, the handover connectivity-smoke stage, `provider_flow`) exist **only** in
  `backend/src` because they straddle the unreconciled seam.

## Objective

One collection seam on the packages stack: the push `RawCollector` + a single `BrokerTick`
(EAV push shape **+** `sequence`), `SessionSupervisor` retained beneath the adapter as the
sole reconnect home, the pull seam retired, and the four use-cases ported and gate-green. Live
capture is content-addressed/idempotent again; live==replay through the same collector.

## What to do

### Step A — Unify the tick and restore idempotency
1. Collapse the two `BrokerTick` definitions into **one** (push shape — `instrument_key`,
   `field_name`, `value: float|str|None`, `underlying`, `provider`, `exchange_ts`,
   `contract_id_broker` — **plus** `sequence`).
2. Wire `event_id = content_event_id(instrument_key, field_name, sequence)` in
   `collectors.normalize` (ADR 0003 idempotency), so live writes are exactly-once on
   re-delivery and kill/restart — not only replay.
3. Retain `connectivity.SessionSupervisor` (backoff, client-id bands, loss-aware
   `GapInterval`) as the session-management layer **beneath** the `MarketDataAdapter`. It no
   longer defines a tick type or a pull loop.

### Step B — Retire the pull seam
4. Remove the pull-side `BrokerSession.ticks()` consumer contract,
   `contracts.broker.BrokerTick`, and `deribit_transport.py`'s pull path **once nothing
   imports them**. import-linter + the gate catch any dangling reference.

### Step C — Port the four use-cases onto the one seam
5. Port `collect_live`, `surface_job`, the handover **connectivity-smoke stage (b)**, and
   `provider_flow` from `backend/src/orchestration` into `infra/orchestration` on the unified
   collector. Remove the two documented `skip`s ADR 0026 left.
6. Wire **Saxo/Deribit onto the Nautilus runtime** through the unified collector (the
   `transport` switch consumer — the live `TradingNode`/collector wiring C1 left open).

## Frozen seam

The unified `BrokerTick` + `MarketDataAdapter` + `RawCollector` is the seam. Once Step A
lands, it is frozen the same way the M0 contracts are: a change to it is a cross-cutting event.

## Test surface

- The `event_id` is content-addressed: the same observation re-delivered after a simulated
  reconnect writes one row (idempotency asserted against the real store, not a fake).
- Live==replay holds on the unified collector: a captured day and its replay produce
  byte-identical `RawMarketEvent` partitions (extends the existing C3 replay gate to the live
  capture path).
- The four ported use-cases run green in the root gate with their `skip`s removed.
- import-linter stays green after the pull seam is deleted (no `packages` reference survives).

## Done criteria

One `BrokerTick`, one collector, one reconnect home; the pull seam gone; the four use-cases
ported and gate-green; Saxo/Deribit live-wired on the runtime. `backend/src/orchestration`
(incl. `surface_job.py`) is now a stale dupe handed to C5.

## Gotchas

- Do not bridge the two seams with a shim "for now" — that is the dual path ADR 0027 forbids.
  The push/pull control-flow impedance is absorbed **inside each adapter**, not above it.
- Delete the pull seam only **after** Step A + C land green — never both-at-once (same rule as
  C5). The `sequence` harvest must precede the deletion, or idempotency regresses.
