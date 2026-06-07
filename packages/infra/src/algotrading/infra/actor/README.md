# infra.actor

The spine: the actor holds **no math**. It transports market state into the analytics
core's pure functions (`snapshots → forwards → iv → surfaces → pricing → risk`), stamps
their outputs with our `ProvenanceStamp`, and persists through the storage port. The same
code runs live and in replay — that identity is the load-bearing invariant of the platform.

## Layout

- `driver.py` — the pure analytics step. `run_analytics(events, positions, …, as_of,
  calc_ts) -> ActorOutputs` is a pure function (no I/O, no clock); `run_day` and
  `persist_outputs` are the disk entry points. **Framework-free on purpose** — it imports
  no `nautilus_trader`, so the math can be driven from anything.
- `outputs.py` — `ActorOutputs`, the frozen container of the eight derived contract tuples.
- `stamping.py` — `build_stamp`, the provenance the actor stamps onto pricing/risk/scenario rows.
- `valuation_join.py` — the math-free join that turns the analytics results into the risk
  engine's per-contract inputs.
- `close_capture.py` — **the daily close-snapshot mode (WS 1C, Part B).** Runs the same pure
  `run_analytics` with `session_open=False` and an injected `as_of` = each enabled index's own
  `session_close(index, trade_date)` (the 1J calendar resolver — Eurex for SX5E, NYSE for SPX,
  never a single global close), then replace-persists one immutable set per `(provider,
  trade_date)`. `capture_daily_close` iterates `enabled_indices()` (never a hardcoded list);
  `make_close_capture` binds the deps into the `(trade_date, baskets) -> results` seam 1G's
  schedule wires. Reads no wall clock, so the close set is byte-identical on replay.
- `nautilus_host.py` — **the runtime spine (ADR 0023 / ADR 0025).** A thin Nautilus
  `Actor` (`AnalyticsActor`) that replays a `RawMarketEvent` stream through Nautilus's engine
  on its simulated clock and drives the unchanged `run_analytics`. `RawMarketEventData` +
  `to_custom_data`/`from_custom_data` bridge our immutable events to/from Nautilus custom data
  (lossless); `run_session_via_nautilus` is the entry point. Our `ParquetStore` stays the
  system of record — Nautilus never owns the raw layer.

## Why live == replay holds under Nautilus

`run_analytics` is pure in its inputs and reads no clock; `as_of`/`calc_ts` are injected. The
Nautilus engine only changes *who feeds the events*, on a simulated clock. The determinism gate
`tests/test_nautilus_replay_byte_identical.py` proves the hosted run returns the same
`ActorOutputs` (stamps included) and writes byte-identical Parquet as a direct `run_analytics`
call. See ADR 0025 for the catalog-topology and provenance-bridging decisions.

IBKR captures through Nautilus's InteractiveBrokers adapter (live wiring to the verifiable
boundary; no TWS Gateway in CI). Saxo/Deribit remain on their vendored capture slice (ADR 0023).
