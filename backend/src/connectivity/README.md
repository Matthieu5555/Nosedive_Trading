# connectivity

The broker-agnostic session seam and the one place reconnect-with-backoff lives.

## TL;DR

Everything broker-specific hides behind one `BrokerSession` Protocol. The rest of
the system speaks `BrokerTick` — plain scalars, no broker enum — so a live IBKR
session, the in-memory fake, and the disk replay are interchangeable. A
`SessionSupervisor` owns the connection: it connects, reconnects on a deterministic
backoff schedule, re-subscribes, records each outage, and hands you a resilient tick
stream. Drive it with an injected `Clock` so backoff and timestamps are deterministic
in tests.

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
    handle(item.tick)                              # a BrokerTick
```

## What's here

- `broker.py` — `BrokerTick`, the `BrokerSession` Protocol, and `content_event_id`,
  the deterministic (SHA-256, cross-process-stable) id used to make re-delivered
  ticks idempotent.
- `supervisor.py` — `SessionSupervisor` (connect/reconnect/re-subscribe, one home for
  retry), `BackoffSchedule`, the `client_id_for` convention, and `GapInterval` /
  `SupervisedTick`.
- `clock.py` — the `Clock` Protocol, real `SystemClock`, and the deterministic
  `ManualClock` that records sleeps instead of performing them.
- `sessions.py` — `FakeBrokerSession` (scripted ticks and drops, drives the whole
  suite) and `ReplayBrokerSession` (re-emits stored events as the same `BrokerTick`,
  recovering each tick's broker contract id from the canonical key so it resolves
  against the universe through the same collector code as a live tick).

## Configuration (operational, not economic)

These are transport knobs, deliberately *not* in `PlatformConfig` (which is economics
only and feeds the reproducibility hash):

- **Backoff** — `BackoffSchedule(base_seconds=1.0, factor=2.0, cap_seconds=30.0)`.
  The documented delay sequence is `1, 2, 4, 8, 16, 30, 30, …` seconds, with no
  jitter so it is exactly assertable. Pass your own schedule to the supervisor.
- **Client-id bands** — `_CLIENT_ID_BANDS` in `supervisor.py` reserves a disjoint id
  band per service (`universe`, `collector`, `replay`, `smoke`) so two services
  connecting to one gateway can never request the same id. Add a band for a new
  service rather than reusing one.

## Gotcha — no live IBKR adapter is vendored

The broker SDK is not a dependency and the spec forbids live IBKR in the suite, so
there is no concrete `IbkrBrokerSession` here. A live session is just one more
implementation of `BrokerSession`; the only broker-specific code it needs is mapping
the broker's native tick-type enum to the string `BrokerTick.field_name`, so the enum
never crosses this boundary. The fake and replay sessions exercise every other path.
