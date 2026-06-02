# Workstream B — Market-data plane

- **Branch:** `feat/market-data-plane`
- **Owns:** `src/connectivity`, `src/universe`, `src/collectors`.
- **Roadmap coverage:** steps 2 (instrument master / universe) and 3 (market-data ingestion), plus the IBKR-session part of step 1.
- **Depends on:** A (the `InstrumentMaster` and `RawMarketEvent` contracts, the storage write path).
- **Blocks:** E (the actor and replay need a broker-agnostic event stream to mirror).

## Objective

Get trustworthy raw data flowing and written down exactly as it arrived. This is
the transport tier: establish and hold the IBKR session, discover the tradable
universe, and capture every observation append-only and loss-aware. No analytics
live here — the roadmap is explicit that the collector callback only normalizes,
stamps, and persists.

Isolate everything broker-specific behind a thin adapter (Part IV.B). The rest of
the system consumes a broker-agnostic event stream and never imports broker
callback enums. This is what lets E's replay emit the same internal events as the
live adapter.

## What you build

1. **Connectivity service.** Owns the IBKR/Nautilus session: connect, heartbeat,
   reconnect with backoff, client-ID convention so services don't collide, pacing
   awareness. Reconnect and retry behavior lives in exactly one place. The session
   can drop and restart without invalidating or corrupting downstream state.

2. **Universe service.** Discover instruments via option-chain APIs; resolve
   broker contract identifiers; validate expiries, strikes, and multipliers;
   normalize expiries to a consistent date format and strikes to numeric; remove
   duplicates deterministically; materialize the canonical `InstrumentMaster` to
   storage, versioned by date and config. Persist the raw broker payload as
   evidence. Treat the broker contract id as an external foreign key, not your
   only identifier. Expose `get_underlying(symbol)`, `get_option_chain(symbol,
   date)`, `resolve_contract(key)`, `load_active_universe(session_date)`.

3. **Collector service.** Subscribe to underlyings and options; normalize each
   incoming tick into a `RawMarketEvent` (instrument key, field, value,
   `exchange_ts` if available, `receipt_ts`, `canonical_ts`); persist every event
   append-only with a session id. Detect pacing/entitlement failures and log them
   as structured events. Emit daily collector summaries: event counts, missing
   intervals, reconnect count, coverage ratios.

4. **Step-1 smoke test.** The end-to-end bootstrap that resolves one contract,
   requests one quote, and writes one event to disk — without placing orders.

## Acceptance criteria

- The collector runs an entire session unsupervised; disconnects produce warnings
  and controlled recovery; a synthetic kill-and-restart does not corrupt the raw
  store; at least one day can be replayed from disk without reaching back to the
  broker.
- The active option universe is reproducible on repeated runs; duplicates removed
  deterministically; multiplier and currency always populated; any unresolved
  contract surfaces as an explicit exception with diagnostics, never silently
  skipped.
- Nothing downstream imports a broker enum; the event stream is broker-agnostic.

## Test surface

Cross-cutting rules live in [TESTING.md](TESTING.md) — read it first. Your tier's
bugs live in wiring and timing, so you are held to behavior tests, not a coverage
number. The non-negotiable one is the kill-and-restart test below.

Connectivity (drive it against a fake/injected broker session and an injected
clock — no live IBKR in the suite):
- Reconnect uses backoff: assert the actual delay sequence against the documented
  schedule with a deterministic injected clock, not a sleep.
- Client-id convention prevents collision: two services request ids, assert they
  differ per the convention.
- A drop-and-restart mid-session does not invalidate or corrupt downstream state.

Raw capture — append-only and loss-aware (this is the invariant you own):
- Kill mid-write, restart: the store holds exactly the events that were durably
  written, with no partial record and no duplicate. Idempotency is keyed on
  `event_id` — a re-delivered tick after reconnect does not double-write.
- A missing interval is recorded as an explicit gap event, never papered over.
- Out-of-order ticks (`exchange_ts` < previous) and ticks with `exchange_ts`
  absent are both handled and stamped; `receipt_ts` and `canonical_ts` are always
  present.
- Replay one stored day from disk produces the same event stream without reaching
  the broker.

Universe:
- Deterministic dedup: the same chain resolved twice yields a byte-identical
  `InstrumentMaster`; duplicate contracts are removed in a defined, stable order.
- An unresolved contract raises an explicit exception carrying diagnostics — a
  test asserts the raise, never a silent skip.
- Multiplier and currency missing from the broker payload is rejected, not
  defaulted.
- Expiry normalization across broker date formats → one canonical form; strike
  coercion to numeric.
- The four accessors (`get_underlying`, `get_option_chain`, `resolve_contract`,
  `load_active_universe`) each have a hit and a miss test.

Broker-agnostic boundary:
- A test asserts the internal event stream type exposes no broker enum — the
  cleanest form is to show the live adapter and a stub replay adapter emit the
  same internal event type, which is exactly what makes E's same-code-path replay
  possible.

Collector summary: event counts, missing intervals, reconnect count, and coverage
ratios are computed correctly against a fixture day with a known, hand-derived
expected summary.

Seam test (you own it, per TESTING.md): `InstrumentMaster` and `RawMarketEvent`
write through A's adapter and read back equal, and a malformed instance is
rejected by A's validation.

## Invariants you own

The immutable, append-only, loss-aware raw layer is yours (written through A's
schema and write path). If data is missing, that fact is recorded, never papered
over. Emit enough metadata that a downstream analytic can later tell whether it
used a fresh or stale observation.

## Gotchas

Never compute analytics inside the collector callback — normalize, stamp, persist,
nothing else. Heavy logic in the callback is the fastest path to dropped events.
Keep the connectivity process isolated from compute so an analytics CPU spike
can't drop market data.
