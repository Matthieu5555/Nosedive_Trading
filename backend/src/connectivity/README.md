# connectivity

The broker-agnostic session seam, and the one place reconnect-with-backoff lives.

## TL;DR

Everything broker-specific hides behind one `BrokerSession` Protocol. The rest of
the system speaks `BrokerTick` — plain scalars, no broker enum — so a live IBKR
session, the in-memory fake, and the disk replay are interchangeable. A
`SessionSupervisor` owns the connection: it connects, reconnects on a deterministic
backoff schedule, re-subscribes everything, records each outage, and hands you a tick
stream that survives drops instead of silently dying. Drive it with an injected
`Clock` so backoff delays and timestamps are deterministic in tests.

## Why this exists

A market-data feed that quietly stops is worse than one that crashes loudly: the
analytics downstream keep running on stale state and nobody notices. "A reliable
IBKR connection that reconnects itself and never silently dies" is one of the named
goals in `BIG_PICTURE.md`, and this module is where that promise is kept. It exists
to do two jobs and only two: keep the connection alive across drops, and keep the
broker's vocabulary from leaking into the rest of the system. Both are here so that
neither has to be reinvented in the collector or anywhere upstream.

## Fastest use

```python
from connectivity import SessionSupervisor, FakeBrokerSession, SystemClock, client_id_for

session = FakeBrokerSession(script=[...])          # or a live BrokerSession impl
supervisor = SessionSupervisor(
    session, client_id=client_id_for("collector"), clock=SystemClock()
)
supervisor.connect()
supervisor.subscribe("o-AAPL-C-100")
for item in supervisor.stream():                   # survives drops, records gaps
    handle(item.tick)                              # item is a SupervisedTick
```

`item.gap_before` is `None` on a normal tick and carries the `GapInterval` that just
ended on the first tick after a reconnect — that is the signal the collector turns
into a durable gap event.

## Public interface

- `broker.py` — `BrokerTick` (the broker-agnostic observation: plain scalars and a
  stable per-session `sequence`), the `BrokerSession` Protocol every concrete session
  implements, and `content_event_id`, the deterministic (SHA-256, cross-process-stable)
  id that makes re-delivered ticks idempotent downstream.
- `supervisor.py` — `SessionSupervisor` (connect / reconnect / re-subscribe, the one
  home for retry), `BackoffSchedule`, `client_id_for`, and the `GapInterval` /
  `SupervisedTick` records the stream yields.
- `clock.py` — the `Clock` Protocol, the real `SystemClock`, and the deterministic
  `ManualClock` that records sleeps instead of performing them.
- `market_data_policy.py` — the broker-neutral feed vocabulary and status. `FeedNotice`
  / `classify_feed_notice` map a broker's numeric error codes into pacing / entitlement /
  other (homed here, re-exported by `collectors`, so the adapter can classify its own
  errors without a `connectivity → collectors` cycle), and `MarketDataStatus` /
  `assess_market_data` pair what was *requested* against what was *effective* and against
  how many subscriptions actually produced — turning a silently-empty feed into a value
  with a `describe()` line that names the likely cause.
- `sessions.py` — `FakeBrokerSession` (scripted ticks and drops, drives the whole
  suite) and `ReplayBrokerSession` (re-emits stored events as the same `BrokerTick`,
  recovering each tick's broker contract id from the canonical key so it resolves
  against the universe through the same collector code as a live tick).

## The connection state machine

The supervisor is a small state machine around one session. `connect()` retries a
failed `connect` on the backoff schedule until it succeeds (or, if a
`max_reconnect_attempts` ceiling is set, gives up and re-raises `ConnectionFailed`).
`stream()` then yields ticks; a `SessionDisconnected` mid-stream is not an error to
the caller — it is caught, the session is reconnected and every remembered
subscription replayed, the outage is recorded as a `GapInterval`, and streaming
resumes with the next tick flagged by that gap.

```
                 connect()
   DISCONNECTED ───────────► CONNECTING
        ▲                    │  │
        │     ConnectionFailed  │ session.connect() ok
        │     → sleep(backoff)  │
        │     ──────┘ (retry)   ▼
        │                    STREAMING ──── tick ───► yield SupervisedTick
        │                    │   ▲                    (gap_before set only on the
        │  ticks() returns   │   │                     first tick after a reconnect)
        │  (clean end)       │   │ reconnect ok:
        ▼                    │   │ re-subscribe all,
       DONE   ◄──────────────┘   │ record GapInterval
                                 │
              SessionDisconnected │
              → record drop time, └── RECONNECTING ── sleep(backoff), session.connect()
```

The diagram omits the option-chain request path (`request_option_chain`, a plain
pass-through to the session) and the bookkeeping counters (`reconnect_count`,
`reconnects`). It shows the happy reconnect; a reconnect's own `connect` can itself
fail and loops through the same backoff retry as the initial connect.

Two properties this buys, both pinned by tests in `test_connectivity.py`: a clean end
of the tick iterator ends the stream with no spurious reconnect, and a drop is
*recovered, not lost* — ticks scripted after the drop still arrive, every prior
subscription is re-subscribed, and the outage shows up as one recorded `GapInterval`.

## State and lifecycle

The supervisor holds mutable state: the list of subscriptions to replay on reconnect,
and `reconnects`, the list of outages seen so far (`reconnect_count` is its length).
The `FakeBrokerSession` additionally tracks a script cursor, connect count, and
subscribe calls so tests can assert exact reconnect and re-subscribe behaviour.
`ManualClock` carries a `_now` it advances by each recorded sleep. Everything else —
`BrokerTick`, `GapInterval`, `SupervisedTick`, `BackoffSchedule` — is a frozen value.

## The trust boundary

This is the system's outer wall against the broker. Two things are deliberately *not*
allowed to cross it inward. First, the broker SDK's types: a live session maps the
broker's native tick-type enum to the plain string `BrokerTick.field_name` inside the
adapter, so no broker enum is ever exported (asserted by
`test_broker_tick_exposes_no_broker_enum`). Second, untrusted *values*: the supervisor
does not validate the content of a tick — that is the next layer's job. The universe
(`resolve_contract`) decides whether a contract id is real, and the collector decides
whether a field name or timestamp is acceptable before anything is persisted. This
module guarantees the *shape* and the *liveness* of the feed; it does not vouch for
the data.

## Configuration (operational, not economic)

These are transport knobs, deliberately *not* in `PlatformConfig` (which is economics
only and feeds the reproducibility hash), per ADR 0003:

- **Backoff** — `BackoffSchedule(base_seconds=1.0, factor=2.0, cap_seconds=30.0)`.
  `delay_for(attempt) = min(cap, base * factor**attempt)`, 0-based, with no jitter, so
  the sequence `1, 2, 4, 8, 16, 30, 30, …` seconds is exactly assertable. A negative
  attempt raises `ValueError`; the exponent is clamped at 32 so a very long outage can
  never overflow a float. Pass your own schedule to the supervisor.
- **Client-id bands** — `_CLIENT_ID_BANDS` in `supervisor.py` reserves a disjoint id
  band per service (`universe`=1000, `collector`=2000, `replay`=3000, `smoke`=9000),
  width 1000, so two services connecting to one gateway can never request the same id
  (a live gateway rejects the second connection that reuses an id). Add a band for a
  new service rather than reusing one; `client_id_for("collector", instance)` draws the
  id for one instance within the band.
- **`max_reconnect_attempts`** (default `None`, meaning retry forever) — the optional
  ceiling on connect retries before `ConnectionFailed` is re-raised.

## Failure modes

All connectivity errors subclass `ConnectivityError` and carry the value that broke.
`SessionDisconnected` and `ConnectionFailed` are *expected operational variants the
supervisor recovers from* — a caller iterating `stream()` never sees them unless a
`max_reconnect_attempts` ceiling is exhausted, in which case `ConnectionFailed`
escapes and the caller must decide to abort. `UnknownServiceError` (no band for a
service name) and `ClientIdError` (instance index outside its band) are caller bugs —
surfaced loudly with diagnostics rather than papered over with a colliding id — and
are not retryable; fix the call.

## The live IBKR adapter

`ibkr_session.py` is the one concrete *live* `BrokerSession`: `IbkrBrokerSession` over
`ib_async` (ADR 0008). It is just another implementation of the Protocol — drive it
through a `SessionSupervisor` like any other session — and it keeps three broker-shaped
things inside the boundary, never letting them cross inward:

1. **The SDK.** `ib_async` is an *optional* extra (`uv sync --extra ibkr`), imported
   lazily inside the methods that hit the gateway. Importing this module or the package
   never needs it, so the gate, the seam tests, and the disk replay all run broker-free.
2. **The native tick-type enum.** IBKR's integer tick types (1=bid, …, plus the 66+
   *delayed* twins a paper login receives) are mapped to the plain `BrokerTick.field_name`
   string here, so a delayed bid and a live bid are indistinguishable downstream.
3. **The chain-discovery shape.** `request_option_chain(symbol)` does **not** leak IBKR's
   `reqSecDefOptParams` parameter grid. It *normalizes* that grid into the broker-neutral
   `universe.AvailableChain`, asks `universe.plan_chain` which bounded set to qualify (the
   selection policy now lives in `universe.chain_planning`, not here), and expands the
   returned `ChainPlan` into resolved, `conId`-keyed contract rows — the underlying plus
   each qualified call/put — in exactly the shape `universe.resolve_chain` /
   `materialize_universe` consumes. (The raw grid is still reachable for diagnostics via
   `option_chain_parameters`, which is deliberately *not* a `BrokerSession` method.)

The session is **read-only** — no order endpoint is ever called — and speaks IB `conId`
as the `broker_contract_id`, so its ticks resolve against the universe through the same
collector path as the fake and the replay.

It also captures **feed diagnostics** for a `MarketDataStatus`: IBKR's `errorEvent` notices
are buffered raw (`feed_errors()`, kept as `(code, message)` so the adapter reads no clock),
and the market-data type the broker actually served is read off the ticks
(`observed_market_data_type` vs `requested_market_data_type`) — so a live request silently
downgraded to delayed, or an OPRA entitlement gap, becomes a structured status instead of
log spam. A caller with a clock classifies the raw notices via `classify_feed_notice`.

The live socket itself is exercised by `backend/scripts/ibkr_live_smoke.py`, not the suite
(the spec bans live IBKR in tests); `tests/test_ibkr_session.py` drives every SDK path
through a fake `ib_async` and asserts the emitted rows are accepted by the real universe
resolver and stream to persisted `RawMarketEvent`s.
