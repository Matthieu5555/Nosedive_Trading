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

1. Run the collection-seam smoke. This exercises the bootstrap end to end with a fake
   adapter — capture a window of quotes through the one push `RawCollector`, stamp each
   tick, write it to the raw layer, place no orders — so it proves the *seam* and the
   collector code path, not the live socket.

   ```
   uv run pytest packages/infra/tests/test_collection_use_cases.py packages/infra/tests/test_collectors.py -q
   ```

   Healthy output: all tests pass. They assert the captured events round-trip, that a
   kill-and-restart writes each content-addressed event exactly once (idempotency), and
   that nothing places an order.

   > **Broker plane (ADR 0023/0024):** IBKR captures via Nautilus's shipped adapter plus a
   > custom Client-Portal REST transport (`packages/infra-ibkr`, `select_ibkr_transport`,
   > REST preferred); Saxo/Deribit via our own adapters (`packages/infra-{saxo,deribit}`).
   > All normalize into the one `contracts.RawMarketEvent` on the unified push seam.

   To prove the *live socket* against a running Gateway/TWS or a broker API — connect
   read-only, expand a bounded option chain, subscribe, and write at least one raw event —
   use the per-broker connect + smoke procedure in
   [`documentation/connectivity/connect-providers.md`](../connectivity/connect-providers.md),
   which also documents each broker's entitlement walls. A standalone repo-root live-smoke
   CLI is a known gap (the pre-merge `ibkr_live_smoke.py` was not ported).

   See ADR 0008/0024 and `../../packages/infra/src/algotrading/infra/connectivity/README.md`.

2. Refresh the universe for the trade date. This resolves the broker's option-chain
   rows into canonical `InstrumentMaster` rows and writes them append-only. It is
   idempotent on the instrument key, so running it twice for the same date is safe.

   ```python
   from datetime import date
   from algotrading.infra.connectivity import SessionSupervisor, SystemClock, client_id_for
   from algotrading.infra.universe import materialize_universe, UniverseService
   from algotrading.infra.storage import ParquetStore

   store = ParquetStore("<data-root>")
   # `session` implements the SupervisedSession protocol (connect / subscribe /
   # option-chain / ticks). For a live feed it is the broker leaf's session
   # (packages/infra-{ibkr,saxo,deribit}); offline, a fake/replay session. The
   # supervisor is the one reconnect home (backoff / client-id / gap recovery).
   supervisor = SessionSupervisor(session, client_id=client_id_for("sod"), clock=SystemClock())
   rows = supervisor.request_option_chain("AAPL")   # one call per configured underlying;
   #                                                  # returns the underlying plus every
   #                                                  # qualified option contract
   materialize_universe(store, rows, date(2026, 6, 1))   # resolve + write masters (idempotent)
   universe = UniverseService.load_active_universe(store, date(2026, 6, 1))
   ```

   In the end-of-day pipeline this is the `refresh_universe` stage
   (`orchestration.refresh_universe`); at start of day you run it standalone to confirm
   the chain resolves. A bad row (missing multiplier or currency, unparseable expiry)
   raises `UnresolvedContractError` naming the offending field — a loud failure, never a
   silent drop. See `../../packages/infra/src/algotrading/infra/universe/README.md`.

3. Start collection for the day. `orchestration.collect_live` wraps the injected push
   `adapter` (the broker leaf's `MarketDataAdapter`) with sequence stamping, builds the
   one `RawCollector` over the store, subscribes, and pumps the feed through the injected
   `drive` callable; each tick is normalized to a `RawMarketEvent`, stamped, and persisted
   append-only. The `session_id` must be stable across restarts (derive it from the trade
   date) — that, with the content-addressed `event_id`, is what makes a kill-and-restart
   write each event exactly once.

   ```python
   from algotrading.infra.orchestration import collect_live

   result = collect_live(
       store=store,
       adapter=adapter,                 # the broker leaf MarketDataAdapter (push)
       subscribe=["o-AAPL-C-100", "o-AAPL-P-100"],
       session_id="2026-06-01",         # stable across restarts → exactly-once on replay
       trade_date=date(2026, 6, 1),
       clock=SystemClock(),
       drive=drive,                     # FeedDriver: pumps the feed to completion
       correlation_id="sod-2026-06-01",
   )
   ```

   The broker leaf supplies the `adapter` and `drive`; for the wired per-broker capture
   recipes see [`documentation/connectivity/connect-providers.md`](../connectivity/connect-providers.md)
   and the `orchestration.provider_flow` façades.

4. Confirm data is flowing. The returned `CollectionResult` summary's event count should
   be climbing and its coverage ratio near 1.0 (every subscribed instrument produced at
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
