# M4 — Market-data plane and the Nautilus actor spine

- **Branch:** `feat/merge-market-data`
- **Owns:** `packages/infra/src/algotrading/infra/{connectivity,collectors,universe,actor}/**` (+ READMEs).
- **Depends on:** M0 (the `BrokerSession` protocol + contracts), M1 (storage to write raw / read replay).
- **Blocks:** M5 (broker adapters plug into the actor wiring), M7 (orchestration drives the actor).

## Objective

Build the broker-agnostic market-data plane and the **Nautilus actor that is the merged system's spine**. This is the load-bearing architectural integration: our Nautilus actor replaces Vincent's hand-rolled `orchestration/pipeline` as the driver, and the same actor runs live and replay. The actor holds no math — it transports market state into M2/M3's pure functions and stamps their outputs into M1's storage.

## What to merge

- **Keep ours (the spine):** the framework-free Nautilus actor (`backend/src/actor/{driver,valuation_join,stamping,outputs}.py`), the broker-agnostic session seam + backoff/reconnect supervisor + client-id convention (`backend/src/connectivity/{broker,sessions,supervisor,clock}.py`), the append-only loss-aware collector with deterministic `event_id` idempotency + gap events + daily summary (`backend/src/collectors/**`), `market_data_policy.py` (entitlement/status, codes 10089/10091), `chain_planning.py`, and deterministic universe dedup + `InstrumentMaster` materialization (`backend/src/universe/**`).
- **Adopt from Vincent:** his collector decomposition where richer — `infra/collectors/{strike_selection,subscription,normalize}.py`, `infra/connectivity/session.py`, and his universe master/discovery split (`infra/universe/{master,discovery,contracts}.py`). Reconcile his `subscription`/`strike_selection` with our `chain_planning` + `ChainSelection` (one chain-selection policy, not two).
- **The wiring decision (resolve in an ADR):** Vincent's broker transports are not Nautilus-based. The actor must run live and replay through Nautilus, so M5's adapters either (a) become custom Nautilus adapters, or (b) write the same immutable raw layer the actor replays from. Pick one here and freeze the adapter-to-actor seam M5 implements against. Nautilus ships an IBKR adapter but **nothing for Saxo/Deribit** — factor that into the choice.

## Frozen seam

Implement M0's `BrokerSession` protocol as the contract M5's three brokers satisfy. Freeze the **actor input** (the normalized market-state bundle the actor consumes) and the **adapter-to-actor wiring** so all three brokers plug in identically and the live path equals the replay path.

## Test surface

Read [TESTING.md] first. Specific to M4:
- The non-negotiable kill-and-restart: a killed collector restarts with no duplicated and no corrupted raw events (deterministic `event_id` idempotency).
- Deterministic universe dedup across processes.
- The actor driven once from a simulated live stream and once from the same events replayed off stored raw → **byte-identical** derived outputs. (This is the headline guarantee; M7 owns the full end-to-end version, but prove it here for the actor in isolation.)
- Broker-agnostic seam: a fake `BrokerSession` drives the full plane → resolver → supervisor → collector → persisted raw, with no broker specifics leaking above the seam.

## Done criteria

One market-data plane, the Nautilus actor as the sole driver (no second pipeline), the `BrokerSession` + actor-wiring seam frozen for M5, gate green with the kill-and-restart and actor-replay-determinism tests. No order placement anywhere.

## Gotchas

Do not keep Vincent's `orchestration/pipeline` as a parallel driver — one spine, the actor. Resist a separate "historical only" code path; live and replay are the same actor or the headline test is a lie. The actor stamps but computes nothing — any math in it belongs in M2/M3. Freeze the adapter seam before M5 starts, or the three brokers will each invent their own.
