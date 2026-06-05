# collectors

The append-only, loss-aware raw layer: subscribe, normalize, stamp, persist — and
nothing else.

## TL;DR

Each broker tick becomes one `RawMarketEvent` and is written append-only through A's
store. The path does exactly three things — normalize, stamp the three timestamps,
persist — and never any analytics (heavy work on the tick path drops data). Writes are
idempotent on a deterministic, content-addressed `event_id`, so a tick re-delivered
after a reconnect, or re-fed after a kill and restart, is written exactly once. Outages
become explicit gap events; pacing/entitlement notices are logged and counted; a daily
summary reports counts, missing intervals, reconnects, and coverage.

## Why this exists

This module owns the invariant the whole platform rests on: the raw layer is sacred —
"write down every tick exactly as it arrived and never edit it" (`BIG_PICTURE.md`).
Everything above can be recomputed from this record, so it has to be complete and it
has to be honest about its own gaps. That is why the collector does so little: the only
safe tick path is a fast one, because any heavy work on the callback is the fastest way
to drop market data. Analytics live elsewhere; this layer just gets every observation —
and every *absence* of one — onto disk, exactly once.

## Fastest use

```python
from collectors import MarketDataCollector, replay_day

collector = MarketDataCollector(
    store=store, universe=universe,
    session_id="2026-06-01",          # STABLE across restarts — that is what makes
    trade_date=date(2026, 6, 1),      # restart idempotent
    clock=SystemClock(),
)
summary = collector.collect(supervisor, subscribe=["o-AAPL-C-100", "o-AAPL-P-100"])

replay_day(store, date(2026, 6, 1))   # the stored day's stream, no broker
```

## Public interface

- `collector.py` — `MarketDataCollector`: reload-seen-on-start, subscribe, persist each
  tick idempotently in atomic flush batches, record gaps, classify feed notices, return
  a `CollectorSummary`. `record_feed_notice` logs and counts a pacing/entitlement notice.
- `normalization.py` — `normalize_tick` (the timestamp and event-id rules),
  `build_gap_event`, `meta_event_id`, and the reserved `GAP_FIELD` / `RESERVED_PREFIX` /
  `is_observation` helpers.
- `summary.py` — `CollectorSummary` and the pure `summarize_session`.
- `notices.py` — a thin re-export of `classify_feed_notice` (pacing / entitlement /
  other) and `FeedNotice`, which now live in `connectivity.market_data_policy` so the
  broker adapter can classify its own error events without a `connectivity → collectors`
  import cycle. The collector still detects and counts feed notices; it just shares one
  classifier with the adapter.
- `replay.py` — `replay_day`: reproduce a stored day from disk, no broker.

## Data flow

```
SupervisedTick (from connectivity.stream)
   │  gap_before set?  ── yes ──►  build_gap_event per subscribed instrument
   │                               ──► enqueue ──► FLUSH (durable before the tick)
   │  resolve_contract(broker_contract_id)        ↑
   │     └─ miss → structured log, skip            │ atomic, all-or-nothing
   │  normalize_tick (stamp 3 timestamps, event_id)│ write through A's store
   ▼     └─ reserved field → log, skip             │
RawMarketEvent ──► enqueue (dedup on event_id) ──► buffer ──► FLUSH every N
   ▼
raw_market_events table ──replay_day──► deterministic stored stream, no broker
```

Read back, the same table feeds the daily summary and `replay_day`. The diagram omits
the end-of-session trailing-gap sweep (a final `_record_gaps` over any outage with no
following tick, deduped by gap id against the inline ones).

## The invariant this owns

The raw layer is immutable, append-only, and loss-aware. Two guarantees hold, and both
are pinned by tests in `test_collectors.py`.

- **Idempotent across re-delivery and restart.** `event_id` is content-addressed on
  `(instrument_key, field, sequence)` (via `connectivity.content_event_id`), so the
  same observation always hashes to the same id — cross-process-stable SHA-256, never
  Python's salted `hash()`. On start the collector reloads the ids already written for
  its session, so re-feeding writes only what is new. Events flush in atomic batches
  through A's all-or-nothing write, and an event is marked "seen" only *after* the flush
  commits — so a crash mid-flush loses the whole in-flight batch (never a partial
  record), and that batch is simply re-fed and re-written on restart. This is the
  non-negotiable kill-and-restart guarantee.
- **Missing data is recorded, never papered over.** Each outage becomes one explicit
  gap event per subscribed instrument (a `RawMarketEvent` under the reserved `__gap__`
  field, value = outage seconds), recorded and flushed the instant the first tick after
  a reconnect reports it — before that tick is enqueued, so the hole is durable no later
  than any observation after it and a crash cannot orphan a post-gap tick. A gap is
  content-addressed on its resumption time, so the inline record and the trailing sweep
  collapse to one. Out-of-order ticks keep their earlier exchange time as
  `canonical_ts`; a tick with no exchange time falls back to the receipt time, and
  `receipt_ts` / `canonical_ts` are always present.

## The three timestamps

`receipt_ts` is always when the collector received the tick (from its clock).
`canonical_ts` — the ordering and as-of time — is the exchange time when the feed
provides one, else the receipt time; an out-of-order tick keeps its earlier exchange
time, because arrival order is plumbing and event order is truth. `exchange_ts` is
required by the contract, so when the feed gives none it also falls back to the receipt
time. One thing these three fields cannot express is *whether the exchange clock was
genuinely present* — that bit is not representable in the current `RawMarketEvent`, and
widening it is an A-owned change (ADR 0003), not a B workaround.

## State and lifecycle

`MarketDataCollector` is single-use per session. It holds: `_seen` (event ids known
durable, seeded on start by `_reload_seen_event_ids`), a `_buffer` of pending events
and its `_buffered_ids`, and a list of classified `_notices`. `collect` subscribes,
drains the supervisor's resilient stream, flushes the tail, sweeps trailing gaps, and
returns the summary. After it returns, the durable state lives entirely in the
`raw_market_events` table; a fresh collector on the same `session_id` and store picks
up exactly where this one left off.

## The trust boundary

The collector is the last gate before untrusted data becomes a permanent record, so it
refuses two kinds of bad tick rather than storing them. A tick for a contract id the
universe doesn't know is a feed anomaly: logged (`tick_for_unknown_contract`) and
skipped, never fatal, so collection keeps running. A tick whose `field_name` starts
with the reserved `__` meta-event prefix is a misconfigured feed that could masquerade
as a gap: it raises `ReservedFieldError` inside `normalize_tick`, which the collector
catches, logs (`tick_with_reserved_field`), and skips. Contract identity itself is
delegated down to the universe (`resolve_contract`); the durable write and its
all-or-nothing atomicity are delegated down to A's store. The collector owns only
normalize-stamp-persist and the loss-aware bookkeeping.

## Configuration

- `flush_every` (default 256) — atomic write-batch size. Must be ≥ 1 (a smaller value
  raises `ValueError`). Smaller is more durable per event but rewrites the partition
  more often; A's append-only store concatenates each partition on write, so very small
  batches over a long session are O(n²) in I/O.
- `session_id` must be stable across restarts (e.g. derived from the trade date), or a
  restart will not recognize already-written events and will double-write.

Feed-notice codes are mapped in `connectivity.market_data_policy` (re-exported through
`notices.py`) — the one place that knows the broker's numbers: pacing `{100, 420}`,
entitlement `{354, 10089, 10091, 10168, 10197}` (the last two delayed-data/not-subscribed
codes included so a downgraded live request is caught), everything else `other`. These are
counted into the summary, not written into the observation stream.

## Failure modes

The tick path is built to *not* fail on bad data — unknown-contract and reserved-field
ticks are logged and skipped (above). What does propagate is a storage write failure:
a failing `_flush` re-raises (e.g. the injected `OSError` in
`test_a_failed_flush_leaves_no_partial_record_then_restart_recovers`), having committed
nothing and marked nothing seen, so the caller can restart and the un-flushed batch is
re-fed and completed. The only error this layer defines is `ReservedFieldError`, and it
is handled internally, never surfaced to the caller.

## Fastest way to exercise it

`backend/tests/test_collectors.py` runs the whole thing against a `tmp_path` store and
the `FakeBrokerSession` / `ReplayBrokerSession` from connectivity — no broker:
`cd backend && uv run pytest -q tests/test_collectors.py`. The kill-and-restart,
failed-flush, gap-durability, and replay paths are all there.

## Gotcha

No analytics in the tick path, ever — normalize, stamp, persist. Keep the connectivity
process isolated from compute so an analytics CPU spike cannot stall the feed.
