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

## What's here

- `collector.py` — `MarketDataCollector`: reload-seen-on-start, subscribe, persist
  each tick idempotently in atomic flush batches, record gaps, classify feed notices.
- `normalization.py` — `normalize_tick` (the timestamp and event-id rules),
  `build_gap_event`, and the reserved `GAP_FIELD` / `is_observation` helpers.
- `summary.py` — `CollectorSummary` and the pure `summarize_session`.
- `notices.py` — `classify_feed_notice` (pacing / entitlement / other).
- `replay.py` — `replay_day`: reproduce a stored day from disk, no broker.

## The invariant this owns

The raw layer is immutable, append-only, and loss-aware. Two guarantees hold:

- **Idempotent across re-delivery and restart.** `event_id` is content-addressed on
  `(instrument_key, field, sequence)`, so the same observation always hashes to the
  same id. On start the collector reloads the ids already written for its session, so
  re-feeding writes only what is new. Events flush in atomic batches through A's
  all-or-nothing write, so a crash mid-flush loses the whole in-flight batch (never a
  partial record) and that batch is simply re-fed and re-written on restart. This is
  the non-negotiable kill-and-restart guarantee.
- **Missing data is recorded, never papered over.** Each outage becomes one explicit
  gap event per subscribed instrument (a `RawMarketEvent` under the reserved `__gap__`
  field, value = outage seconds), recorded and flushed the instant the first tick after
  a reconnect reports it — before that tick is enqueued, so the hole is durable no later
  than any observation after it and a crash cannot orphan a post-gap tick. Out-of-order
  ticks keep their earlier exchange time as `canonical_ts`; a tick with no exchange time
  falls back to the receipt time, and `receipt_ts` / `canonical_ts` are always present.

## Configuration

- `flush_every` (default 256) — atomic write-batch size. Smaller is more durable per
  event but rewrites the partition more often; A's append-only store concatenates each
  partition on write, so very small batches over a long session are O(n²) in I/O.
- `session_id` must be stable across restarts (e.g. derived from the trade date), or a
  restart will not recognize already-written events.

## Gotcha

No analytics in the tick path, ever — normalize, stamp, persist. Keep the connectivity
process isolated from compute so an analytics CPU spike cannot stall the feed. Field
names beginning with `__` are reserved for meta-events; a broker tick using one is
logged and skipped, never stored.
