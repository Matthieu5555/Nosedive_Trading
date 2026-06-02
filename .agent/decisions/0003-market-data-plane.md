# 0003 — Market-data plane: idempotency identity, gap encoding, broker seam

- **Status:** accepted
- **Date:** 2026-06-01

## Context

Workstream B (`src/connectivity`, `src/universe`, `src/collectors`) builds the
transport tier on A's contracts without modifying them. Several choices are not
obvious from the code and would otherwise be reverse-engineered — or re-litigated —
by the next agent, especially E, which mirrors B's event stream in replay. They are
recorded here. B owns no contract; everything it persists goes through A's
`RawMarketEvent` and `InstrumentMaster` and A's append-only write path.

## Decision

1. **Idempotency is content-addressed, scoped by a stable session.** A raw event's
   identity is A's primary key `(session_id, event_id)`. `event_id` is
   `content_event_id(instrument_key, field, sequence)` — SHA-256 of canonical content,
   not a uuid and not Python's salted `hash()` — so the *same observation* always
   hashes to the *same id*. The feed's per-session `sequence` (stable across
   re-delivery) distinguishes otherwise-identical ticks and lets a tick replayed after
   a reconnect collapse onto its original. `session_id` must be **stable across
   restarts**; on start the collector reloads the ids already written for its session,
   so re-feeding writes only what is new. Together these make re-delivery and
   kill-and-restart write each event exactly once, enforced as a hard floor by A's
   append-only store.

2. **A missing interval is a gap meta-event inside `RawMarketEvent`.** B cannot add a
   table (contracts are A-owned), but the loss-aware record must be durable and
   queryable. A gap is therefore a `RawMarketEvent` under the reserved field name
   `__gap__` (value = outage seconds), one per subscribed instrument per outage.
   Field names beginning with `__` are a reserved meta-event namespace; `is_observation`
   filters them, and a broker tick that tries to use one is logged and skipped. The gap
   is recorded and flushed the instant the first tick after a reconnect reports it —
   before that tick is enqueued — so it is durable no later than any observation after
   it; a crash can never leave a post-gap observation on disk with no record of the hole,
   even though a fresh supervisor on restart has no memory of the past outage. A trailing
   outage with no following tick is caught by an end-of-session sweep, deduped by the
   gap's content-addressed id against the inline one.

3. **`exchange_ts` falls back to the receipt time when the feed gives none.** The
   contract types `exchange_ts` as a required `datetime`, so it cannot be stored null
   (the read path would refuse it). When the feed provides no exchange time,
   `canonical_ts` and `exchange_ts` both take the receipt time, and `canonical_ts` is
   always the as-of/ordering time. The one thing this cannot express is *whether the
   exchange clock was genuinely present*; that bit is not representable in the current
   `RawMarketEvent`, and widening it is an A-owned change, not a B workaround.

4. **The broker seam carries plain scalars only.** `BrokerTick` has no enum and no
   broker SDK type; a broker's native tick-type enum is mapped to the string
   `field_name` inside the adapter. `ReplayBrokerSession` re-emits stored events as the
   same `BrokerTick` the live adapter does, recovering each tick's broker contract id
   from the stored event's canonical instrument key (the key embeds it verbatim, via
   `contracts.broker_contract_id_from_canonical`) so a replayed tick resolves against the
   universe through the *same* `resolve_contract` path as a live tick — not skipped as an
   unknown contract. This is what lets E run the same collector/supervisor code over
   replay. (An earlier draft placed the canonical key itself in the broker-id slot, which
   the live collector could not resolve; the inverse-of-`canonical()` recovery is what
   makes same-code-path replay actually run, not merely type-check.)

## Alternatives considered

- **Random/uuid `event_id`.** Simple, but a re-delivered tick would get a new id and
  double-write, and a restart could not recognize already-written events. Rejected: it
  defeats the one guarantee this tier exists to provide.
- **A dedicated gap/notice table.** Cleaner typing, but adding a table is an A-owned
  contract change and a cross-workstream ripple. The reserved-field encoding keeps the
  loss-aware record durable within A's existing seam. If gaps ever need rich structure,
  that is a routed request to A. Recorded so the tradeoff is visible.
- **Pacing/entitlement as durable events too.** The spec deliberately splits these: a
  missing data interval is a durable gap event; a pacing/entitlement failure is a
  *structured log* plus a count in the daily summary. B follows that split rather than
  writing feed-health noise into the observation stream.
- **Operational settings in `PlatformConfig`.** The backoff schedule, client-id bands,
  and flush batch size are transport knobs. They are kept out of `PlatformConfig`
  (economics only) so they never move the reproducibility hash, matching A's rule that
  environment is not economics.

## Consequences

- E can mirror B's stream in replay through the same code path, because the only thing
  crossing the seam is `BrokerTick` (no broker enum) and the persisted form is A's
  `RawMarketEvent`. `replay_day` reproduces a stored day from disk with no broker.
- Downstream analytics reading `raw_market_events` must skip reserved `__`-prefixed
  fields (use `collectors.is_observation`); a gap is data-about-absence, not an
  observation.
- The transport tier is held to behaviour tests (no live IBKR in the suite); the
  non-negotiable kill-and-restart, deterministic dedup, broker-agnostic seam, and the
  B→A seam round-trip are all pinned by tests in `backend/tests/test_{collectors,
  universe,connectivity,smoke_bootstrap,seam_market_data}.py`.
- No concrete IBKR/Nautilus session is vendored; it is one more `BrokerSession`
  implementation whose only broker-specific job is mapping the native tick-type enum to
  `field_name`. That gap is stated, not claimed away.
