# infra.collectors

The one collection seam (ADR 0027): turn a broker's market-data stream into the immutable,
append-only `RawMarketEvent` raw layer, exactly once.

## TL;DR

A broker adapter is a push `MarketDataAdapter` (`subscribe` / `set_tick_callback` /
`set_fault_callback` / `unsubscribe_all`) that turns vendor callbacks into the one
`BrokerTick`. The `RawCollector` normalizes each tick into a `contracts.RawMarketEvent`,
content-addresses its `event_id`, and persists batches into the `ParquetStore`. The same
collector records a live feed and (via `ReplaySource`) a stored day — one code path, so
live==replay holds.

```python
collector = RawCollector(
    store=store, adapter=adapter, session_id="sess-2026-06-05",
    trade_date=date(2026, 6, 5), clock=clock, subscribed_keys=keys,
)
collector.start(keys)        # subscribe; ticks then flow in via the callback
...                          # the adapter pushes; on a reconnect call record_reconnect(gap)
summary = collector.close()  # flush, unsubscribe, return the daily CollectorSummary
```

## What lives here

- **`normalize.py`** — the one `BrokerTick` (push EAV shape: `instrument_key`, `field_name`,
  `value`, `underlying`, `sequence`, `provider`, `exchange_ts`, `contract_id_broker`) and
  `normalize_event`, which stamps a tick into a `contracts.RawMarketEvent` with
  `event_id = content_event_id(instrument_key, field_name, sequence)`. An absent value (None /
  non-finite / categorical) is reported as a skip, not stored as a fake zero.
- **`collector.py`** — `RawCollector` (the capture path: reload-seen-ids, dedup buffer, atomic
  flush, gap recording via `record_reconnect`), the `MarketDataAdapter` protocol, and `FeedFault`.
- **`normalization.py`** — the loss-aware gap meta-event (`build_gap_event`, `meta_event_id`,
  `GAP_FIELD`), content-addressed on its resumption time so a reproduced outage writes once.
- **`live.py`** — `SequenceStamping`, the wrapper that assigns each live tick the per-(instrument,
  field) ordinal the content-addressed id needs (the same rule the replay source uses).
- **`replay.py`** — `replay_day` (read a stored day in canonical order) and `ReplaySource` (a push
  adapter that re-emits stored events through the same collector; re-capture is exactly-once).
- **`summary.py`** / **`notices.py`** — the daily `CollectorSummary` and feed-notice classification.
- **`transport_seam.py`** — `SupportsRestGet` / `SupportsRest`, the one REST transport protocol
  every polling collector consumes (it used to be copy-pasted seven times across the broker
  leaves — audit M40). `runtime_checkable`; concrete transports (`CpRestTransport`,
  `SaxoTransport`) satisfy it structurally, tests satisfy it with a fake.
- **`ws_listener.py`** — `WebSocketListener`, the one WS subscription lifecycle (owned daemon
  thread, stop event, reconnect via the `websockets` iterator, fault callback) both streaming
  leaves run on. Hoisted from byte-identical twins in infra-saxo/infra-deribit (audit M26); the
  leaf `connectivity.ws_listener` modules are thin re-exports. `websockets` is imported lazily,
  inside the listen loop — the broker leaves declare the dependency, this package does not.

## Idempotency — how capture is exactly-once

`event_id` is content-addressed on `(instrument_key, field_name, sequence)` (ADR 0003), so a tick
re-delivered after a reconnect, or re-fed after a kill/restart, hashes to the *same* id and the
append-only store keeps one copy. `sequence` is the feed's stable per-(instrument, field) ordinal:
`SequenceStamping` assigns it on the live path and `ReplaySource` re-derives it in canonical order,
so a captured day and its replay produce the same ids — proven against the real store in
`tests/test_collectors.py` and `tests/test_collection_use_cases.py`.

Broker-*session* reconnect/backoff and the loss-aware `GapInterval` live in
`connectivity.SessionSupervisor`, *beneath* the adapter — the collector never owns session
reconnect. The one exception this package does own is the WS transport's own reconnect loop
(`ws_listener.py` above): a dropped socket re-enters the `websockets` reconnect iterator and the
drop is surfaced through the fault callback, which is what feeds the gap meta-event machinery.
