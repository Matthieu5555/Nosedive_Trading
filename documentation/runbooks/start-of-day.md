# Start of day

## What this is for

Prove the platform can talk to the broker, resolve the universe, and capture market
data before the trading day depends on it. The goal is to catch a dead connection, a
stale universe, or a missing entitlement while there is still time to fix it — not at
end of day when you have a hole in the raw layer you cannot refill.

## When you run it

Once, before the session you intend to collect. If the connectivity smoke fails, stop
and fix it before going further; collection on top of a broken session just produces an
empty or holey raw layer.

## Steps

Everything runs from the repo root against the `algotrading.infra` packages. Sync first
if you have not today.

```
uv sync
```

1. Run the connectivity smoke test. This is the bootstrap end to end: resolve one
   contract off a broker session, request one quote, write one event, place no orders.
   It uses the in-memory fake session, so it proves the *seam* and the collector code
   path, not the live socket.

   ```
   uv run pytest tests/test_smoke_bootstrap.py -q
   ```

   Healthy output: all tests pass. The smoke asserts exactly one event is written, its
   value round-trips, and the positions layer stays empty (nothing places an order).

   > **ADR 0023 (2026-06-05):** under the new direction IBKR connectivity moves to Nautilus's
   > shipped adapter; the `IbkrBrokerSession` / `ibkr_live_smoke.py` path below is the current
   > (pre-migration) one, kept until C1 lands the Nautilus runtime.

   To prove the *live socket* against a running Gateway/TWS — connect read-only, expand a
   bounded option chain, subscribe, and write at least one raw event — you would run the live
   IBKR smoke (it needs the optional broker SDK and a reachable gateway). That manual smoke
   script lived in the retired `backend/scripts/` tree (`ibkr_live_smoke.py`) and was *not*
   ported to the monorepo, so there is no current entrypoint for it — treat live-socket
   smoke as a known gap until it is reinstated.

   Healthy output ended with an `OK:` line and a non-zero event count; any failure printed a
   `FAIL:` line naming the step (connect, qualify, materialize, or no events) and exited
   non-zero. See ADR 0008 and `../../packages/infra/src/algotrading/infra/connectivity/README.md`.

2. Refresh the universe for the trade date. This resolves the broker's option-chain
   rows into canonical `InstrumentMaster` rows and writes them append-only. It is
   idempotent on the instrument key, so running it twice for the same date is safe.

   ```python
   from datetime import date
   from connectivity import SessionSupervisor, SystemClock, client_id_for
   from universe import materialize_universe, UniverseService
   from storage import ParquetStore

   store = ParquetStore("<data-root>")
   # `session` is a BrokerSession implementation: IbkrBrokerSession for a live feed
   # (needs `uv sync --extra ibkr` and a running gateway), or FakeBrokerSession /
   # ReplayBrokerSession with no broker. The supervisor wraps it with reconnect/heartbeat.
   #   from connectivity import IbkrBrokerSession
   #   session = IbkrBrokerSession(host="127.0.0.1", port=4002)  # read-only
   supervisor = SessionSupervisor(session, client_id=client_id_for("sod"), clock=SystemClock())
   rows = supervisor.request_option_chain("AAPL")   # one call per configured underlying;
   #                                                  # for IBKR this returns the underlying
   #                                                  # plus every qualified option contract
   materialize_universe(store, rows, date(2026, 6, 1))
   universe = UniverseService.load_active_universe(store, date(2026, 6, 1))
   ```

   In the end-of-day pipeline this is the `refresh_universe` stage
   (`orchestration.refresh_universe`); at start of day you run it standalone to confirm
   the chain resolves. A bad row (missing multiplier or currency, unparseable expiry)
   raises `UnresolvedContractError` naming the offending field — a loud failure, never a
   silent drop. See `../../packages/infra/src/algotrading/infra/universe/README.md`.

3. Start collection for the day. The collector subscribes, normalizes each tick to a
   `RawMarketEvent`, stamps it, and persists append-only. The `session_id` must be
   stable across restarts (derive it from the trade date) — that is what makes a
   kill-and-restart write each event exactly once.

   ```python
   from collectors import MarketDataCollector
   from connectivity import SystemClock

   collector = MarketDataCollector(
       store=store, universe=universe,
       session_id="2026-06-01", trade_date=date(2026, 6, 1), clock=SystemClock(),
   )
   summary = collector.collect(supervisor, subscribe=["o-AAPL-C-100", "o-AAPL-P-100"])
   ```

4. Confirm data is flowing. `summary.event_count` should be climbing and
   `summary.coverage_ratio` should be near 1.0 (every subscribed instrument produced at
   least one observation). For a live view across underlyings, build the dashboard (see
   the [intraday health runbook](intraday-health.md)).

## Healthy output

The smoke test passes; the universe materializes without raising; the collector summary
shows a non-zero `event_count`, a `coverage_ratio` at or near 1.0, and `gap_count` 0.

## When a step fails

- Smoke test fails: the connectivity seam is broken. Do not start collection. Go to the
  [incident-response runbook](incident-response.md), "connectivity" row.
- Universe refresh raises `UnresolvedContractError`: a broker row is malformed. The
  error carries the verbatim payload and the offending field — fix the source or
  exclude the contract; do not default the missing field.
- Collector shows `coverage_ratio` well below 1.0 or a rising `gap_count`: the feed is
  thin or dropping. This is the `check_collector_continuity` and
  `check_underlying_quote_health` QC checks' territory; see the
  [incident-response runbook](incident-response.md) and the
  [QC README](../../packages/infra/src/algotrading/infra/qc/README.md).
