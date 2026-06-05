# infra.connectivity

Broker-agnostic session management: the one reconnect home beneath the adapter, the client-id
convention, the clock, and the market-data entitlement policy.

## TL;DR

The push collection seam (`collectors.MarketDataAdapter` + `RawCollector`, ADR 0027) is where
ticks flow. This package sits *beneath* the adapter and owns only the session lifecycle — it does
not define a tick type or a pull loop.

- **`supervisor.py`** — `SessionSupervisor`, the single home for connect/reconnect-with-backoff
  (`BackoffSchedule`, deterministic, no jitter), the client-id convention (`client_id_for`, disjoint
  per-service bands), and loss-aware outage recording (`GapInterval`). `recover(dropped_at)`
  reconnects, re-subscribes, and returns the `GapInterval` the collector turns into a gap meta-event.
  It manages a minimal `SupervisedSession` (connect / disconnect / is_connected / subscribe /
  request_option_chain) — no `.ticks()`.
- **`session.py`** — `BrokerSession`, the connection *lifecycle* state machine (open/heartbeat/
  reconnect over a `BrokerTransport`), used by the IBKR leaf. Distinct from the supervisor: this is
  transport health, that is reconnect-and-gap policy.
- **`clock.py`** — the injected `Clock` (`ManualClock` for tests, `SystemClock`); nothing here reads
  a wall clock directly.
- **`market_data_policy.py`** — entitlement/health assessment (`MarketDataStatus`,
  `assess_market_data`) and feed-notice classification (`FeedNotice`, pacing/entitlement/other).
- **`content_event_id`** is re-exported from the frozen `contracts` seam — the idempotency primitive
  the collector hashes its event ids with.

## The pull seam is gone (ADR 0027)

The old pull collection seam — `contracts.broker.BrokerTick`, the `BrokerSession.ticks()` consumer
protocol, the `FakeBrokerSession`/`ReplayBrokerSession` pull sessions, and `SessionSupervisor.stream()`
— has been retired. Its two genuinely-better parts were harvested first: `sequence`-based
content-addressed idempotency (now on the one `collectors.BrokerTick`) and `SessionSupervisor` itself,
kept here as the reconnect home. No broker SDK type crosses this boundary; the live adapters live in
the `infra-{ibkr,saxo,deribit}` leaves.
