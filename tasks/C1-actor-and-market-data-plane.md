# C1 — Land the market-data plane + actor spine in `packages/infra`, resolve the broker fork

- **Owns:** `packages/infra/src/algotrading/infra/{connectivity,collectors,universe,actor}/**` (+ READMEs), `packages/infra-{ibkr,saxo,deribit}/**`, and one new ADR recording the resolution.
- **Depends on:** M0 (`contracts.BrokerSession` + scalar `BrokerTick`, `contracts.RawMarketEvent`), M1 (the raw layer / replay). Both landed.
- **Blocks:** C2 (qc reads actor outputs), C3 (orchestration drives the actor; the headline tests need it), C5 (retiring `backend/{actor,connectivity,collectors,universe}`).
- **State going in:** the consolidated M4 plane + the framework-free actor exist **only in `backend/src`**; they were never relocated. `packages/infra/{collectors,universe,connectivity}` currently hold a **second, forked copy** — Vincent's slice vendored near-verbatim by M5 — which defines a parallel `BrokerTick` and a selection-less universe. This is the one live architectural conflict and the source of the only red tests on the root gate (3 Saxo-config failures).

## Objective

One market-data plane, one actor, one broker seam — in `packages/infra`. The actor is the system's spine: it holds no math, transports market state into M2/M3's pure functions, stamps their outputs, and persists through M1. **The same actor runs live and replay** — that identity is the load-bearing invariant the whole merge rests on.

## What to do

1. **Relocate M4's canonical content** from `backend/src/{actor,connectivity,collectors,universe}` into the `packages/infra` namespace (`algotrading.infra.*` imports). This is the version that wins — it is the one consolidated build:
   - **actor:** `driver.py` (`run_analytics`/`run_day`), `stamping.py`, `outputs.py` (`ActorOutputs`), `valuation_join.py`.
   - **connectivity:** the broker-agnostic session seam, backoff/reconnect supervisor, client-id convention, `market_data_policy.py` (entitlement/status codes 10089/10091), clock.
   - **collectors:** the append-only, loss-aware collector with deterministic `event_id` idempotency, gap events, daily summary, and `replay_day` (canonical-order replay).
   - **universe:** the **one** chain-selection policy `chain_planning.py` — `plan_chain` (discovery) + `select_capture_keys` (capture) over a single `ChainSelection` — plus deterministic dedup and `InstrumentMaster` materialization.
2. **Delete the M5 vendored fork** now occupying those dirs in `packages/infra`: `collectors/{collector,normalize}.py`, `universe/{contracts,discovery,master}.py`, `connectivity/session.py`, and especially the **second `BrokerTick`** in `collectors/normalize.py`. These bypass the frozen seam and re-introduce a parallel selection-less universe. Salvage only genuinely-additive Vincent pieces that do **not** duplicate M4 (e.g. Saxo `auth/`, per-broker `config.py`/sample configs).
3. **Retarget the broker leaves** `infra-{ibkr,saxo,deribit}` onto the frozen seam:
   - import `contracts.BrokerTick` / `contracts.BrokerSession`, **not** `infra.collectors.normalize.BrokerTick`;
   - discovery emits `universe.AvailableChain` rows feeding the one `chain_planning` policy — the adapter supplies the menu, it does not invent selection;
   - keep the leaf packages (ADR 0012) and their current "minus `flow.py`" state (`flow.py` needs the analytics pipeline that lands via the actor — defer);
   - fix the 3 red Saxo-config tests as part of the retarget.
4. **Record the resolution as a new ADR** (next free number). ADR 0020 is *accepted* and the board admits M5 "knowingly contests" it; make the resolution as loud as the fork was. One `BrokerSession` seam, one `chain_planning` policy, one collector writing the raw layer, one actor replaying it.

## Frozen seam (unchanged — only import paths move)

`contracts.BrokerSession` (`connect`/`disconnect`/`is_connected`/`request_option_chain`/`subscribe`/`ticks`, emitting only scalar `BrokerTick`) + the M1 raw layer. **Actor input** = the `RawMarketEvent` stream read off the raw layer in canonical order via `collectors.replay_day`, plus injected `as_of`/`calc_ts`/`config`/`config_hash` and the `InstrumentMaster` set. No broker SDK type and no pre-normalized bundle crosses the seam; the actor builds the normalized state itself inside `run_analytics`. Per ADR 0020, this is what makes live == replay and lets all three brokers plug in identically. No `nautilus_trader` dependency.

## Test surface (in `packages/infra/tests` + per-leaf tests)

Read `tasks/TESTING.md` first.
- **Kill-and-restart idempotency:** a killed collector restarts with no duplicated and no corrupted raw events.
- **Deterministic universe dedup** across processes.
- **Actor byte-identical in isolation:** drive the actor once from a simulated live stream and once from the same events replayed off stored raw → byte-identical derived outputs. (C3 owns the full end-to-end version; prove the actor alone here.)
- **Broker-agnostic seam:** a fake `BrokerSession` drives the full plane → resolver → supervisor → collector → persisted raw, with no broker specifics leaking above the seam.
- **Per-leaf conformance:** each of IBKR/Saxo/Deribit *is-a* `contracts.BrokerSession` (structural check) and maps its native tick enum to the string `field_name` inside the adapter.

## Done criteria

One market-data plane and the actor as the **sole** driver, both in `packages/infra`; the vendored fork deleted; the three brokers on the frozen seam; the new ADR written; **root gate green (the 3 Saxo failures cleared)**. `backend/{actor,connectivity,collectors,universe}` are now stale dupes — hand them to C5 for retirement.

## Gotchas

One spine — the actor. No second driver, no "historical only" path, or the byte-identical guarantee becomes a lie. The actor stamps but computes nothing; any math in it belongs in M2/M3. Do not re-vendor: the fix is *one* policy and *one* `BrokerTick`, not a reconciliation of two.
