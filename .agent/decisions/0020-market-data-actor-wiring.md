# 0020 — Market-data actor wiring: raw-layer replay, and the seam M5 implements

> **AMENDED 2026-06-13 (index-only, [[0042-index-options-only-scope-ibkr-sole-broker]]).**
> Doubly-dead, historical record only: this ADR was already superseded by
> [[0023-nautilus-runtime-spine-and-library-leverage]] (2026-06-05), and the multi-broker premise
> it argues over ("three brokers IBKR/Saxo/Deribit") is now moot — Saxo and Deribit were removed
> entirely; IBKR is the sole live broker. Read it for history, not for current wiring.

- **Status:** **superseded by [[0023-nautilus-runtime-spine-and-library-leverage]]** (2026-06-05)
  — Nautilus is now the runtime spine and Saxo/Deribit keep their own adapters; the
  no-`nautilus_trader` decision (§1) and the "all three brokers on the scalar `BrokerSession`" seam
  (§2) are reversed. The collection seam is finally settled in
  [[0027-collection-seam-push-canonical]] (the push `RawCollector`/EAV `BrokerTick` is canonical;
  this ADR's scalar pull `BrokerSession.ticks()` seam is retired). The live==replay guarantee
  (§1, raw-layer replay) is upheld there. Original text kept for the record.
- **Date:** 2026-06-05
- **Workstream:** M4 (market-data plane + actor spine)

## Context

M4 owns the load-bearing integration: one market-data plane and the actor that is the
system's spine, driving the same pure functions live and in replay. The spec leaves one
decision open and demands it be resolved here, because M5's three brokers (IBKR, Saxo,
Deribit) all build against whatever this freezes:

> Vincent's broker transports are not Nautilus-based. The actor must run live and replay
> through Nautilus, so M5's adapters either (a) become custom Nautilus adapters, or (b)
> write the same immutable raw layer the actor replays from. Pick one and freeze the
> adapter-to-actor seam. Nautilus ships an IBKR adapter but **nothing for Saxo/Deribit**.

Two facts decide it. First, the blueprint is the plan of record (ADR 0011) and it never
mentions Nautilus — it prescribes a connectivity service, a collector writing an
**append-only raw layer**, and a **single code path for live and replay** ("the canonical
run order applies both to live and replay, the only difference being the source of
events", Part IV §F; "resist a separate historical-only implementation", Step 13 / Part
XVIII). Second, our actor is already framework-free, and ADRs 0003 (scalar `BrokerSession`
seam), 0007 (no dual-path fork), and 0016 (`EventSource`: three sources, one pipeline)
already lay the track. This ADR records the choice and consolidates those into the one
seam M5 implements, so the three brokers do not each invent their own.

## Decision

**1. Wiring is raw-layer replay (option b), not custom Nautilus adapters (option a).**
Every broker adapter's whole job is to be a `BrokerSession` and to translate native chain
discovery; the collector stamps its ticks into the append-only `RawMarketEvent` layer, and
the actor replays from that layer. Live equals replay because both feed `run_analytics`
the same `RawMarketEvent` stream off the same raw layer. **No `nautilus_trader` dependency
is added.** "Nautilus actor" in this codebase names the actor *pattern* — a thin driver
that transports market state into pure functions and stamps their outputs (see
`actor/README.md`) — not the framework. Option (a) is rejected: Nautilus provides no
Saxo/Deribit adapter, so two of three brokers would be hand-rolled against a framework
that buys nothing here; it contradicts the blueprint; and it risks a second,
framework-coupled code path. The raw layer already gives uniform live==replay for all
three brokers.

**2. The frozen adapter-to-actor seam — three obligations, nothing more.** A broker added
in M5 satisfies exactly these and inherits everything else:

- **(a) Implement `contracts.BrokerSession`** (`connect`/`disconnect`/`is_connected`/
  `request_option_chain`/`subscribe`/`ticks`), emitting only scalar `BrokerTick`. The
  native tick-type enum is mapped to the string `field_name` *inside* the adapter; no
  broker SDK type crosses the seam (ADR 0003 §4).
- **(b) Translate native chain discovery into `universe.AvailableChain` rows.** *Which*
  contracts to qualify and stream is the shared, broker-neutral chain-selection policy
  (`universe.chain_planning`), not a per-broker heuristic: `plan_chain` for discovery,
  `select_capture_keys` for capture, both over one `ChainSelection`. The adapter supplies
  the menu; it does not invent the selection.
- **(c) Write nothing downstream.** The adapter produces ticks; the collector normalizes
  and stamps them into `RawMarketEvent` on the append-only raw layer; the actor reads that
  layer. An adapter never touches snapshots, analytics, or storage tables.

Its replay counterpart is free: `ReplayBrokerSession` re-emits stored `RawMarketEvent` as
the same `BrokerTick` the live adapter does, recovering `broker_contract_id` from the
canonical key (ADR 0003 §4), so the collector resolves a replayed tick through the same
path as a live one.

**3. The actor input is the `RawMarketEvent` stream off the raw layer** (read in canonical
order via `collectors.replay_day`), plus injected `as_of`/`calc_ts`/`config`/`config_hash`
and the `InstrumentMaster` set. The actor is handed no broker-specific bundle and no
pre-normalized live bundle — it builds the normalized market state (snapshots) itself,
inside the pure `run_analytics`. That is precisely what makes the live path equal the
replay path and lets all three brokers plug in identically.

## Alternatives considered

- **Custom Nautilus adapters (option a).** Rejected for the three reasons in §1. Recorded
  because the spec explicitly poses it and the next agent should not re-open it without new
  facts (e.g. a real need for Nautilus's execution-venue simulation in a later strategy
  layer — which would still be one more `BrokerSession` writing the same raw layer, not a
  rewrite).
- **A per-broker selection policy.** Vincent's `collectors/subscription.py` +
  `strike_selection.py` and our `universe/chain_planning.py` were two policies for one job.
  Rejected: one `ChainSelection` over the broker-neutral shapes is the single policy M4
  set out to leave behind, with discovery and capture as its two stages.
- **Two bundles — a live bundle and a replay bundle handed to the actor.** Rejected: two
  bundles is two code paths waiting to drift (ADR 0007). The raw layer is the one bundle.

## Consequences

- M5's three brokers each implement one protocol plus one chain-discovery translation and
  write the raw layer; they share discovery+capture selection and get replay for free. No
  broker can leak a second code path or its own selection policy above the seam.
- No `nautilus_trader` dependency enters the workspace; the "spine" is the framework-free
  driver already in `backend/src/actor`.
- Chain-selection is now one policy with two stages in `universe.chain_planning`:
  `plan_chain` (discovery — bound the qualify request from a broker's raw chain menu) and
  `select_capture_keys` (capture — pick the nearest-the-money keys to stream within the
  broker's pacing budget). Vincent's `subscription`/`strike_selection` are folded in over
  our `ChainSelection`/`InstrumentKey`, not vendored as a parallel module.
- The seam is the M0-frozen `contracts.BrokerSession` plus the M1 raw layer (both now
  landed in `packages/infra`). When M4 relocates there, only import paths change — the
  seam M5 builds against does not.
