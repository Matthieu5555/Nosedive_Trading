# Replay and backfill

## What this is for

Rebuild derived analytics from the immutable raw layer — for one day or a date range —
using the *identical* compute path as live. Reach for this to fill a missing analytic
partition, to restate a range under a new code or config version without destroying the
old numbers, or to prove that a replay reproduces the live outputs.

The load-bearing fact: reconstruction is not a second engine. It is `actor.run_day`'s
compute run over stored days. Live and replay differ only in who populated the raw layer
first — never in the math (ADR 0007, decision 4). That is why a replay can be trusted to
match live, and it is what the byte-identical headline test pins.

## When you run it

- An analytic partition is missing (the dashboard or a `missing_partition` alert flagged
  a `(trade_date, underlying)` gap) and the raw layer for that day is present.
- You changed code or config in a way that affects the numbers and need to restate a
  historical range under a version, leaving the old analytics intact for comparison.
- You want to verify a day's replay still equals what was computed live.

## Steps

Everything runs from `backend/`. Point reconstruction at a store that already has raw
events on disk.

1. See which days *can* be replayed (the days that have a stored raw partition):

   ```python
   from orchestration.reconstruction import stored_trade_dates
   from storage import ParquetStore

   store = ParquetStore("<data-root>")
   stored_trade_dates(store)        # ascending tuple of replayable dates
   ```

2. Reconstruct one day (the "trigger a replay" step):

   ```python
   from datetime import UTC, date, datetime
   from orchestration.reconstruction import reconstruct_day

   day = reconstruct_day(
       store, date(2026, 6, 1), positions,
       instruments=instruments, masters=masters,
       config=config, config_hash=config_hash,
       as_of=datetime(2026, 6, 1, 15, 30, tzinfo=UTC),     # market/valuation time
       calc_ts=datetime(2026, 6, 1, 16, 0, tzinfo=UTC),    # computation time, stamped
       persist=True,
   )
   print(day.status)          # RECONSTRUCTED / EMPTY / MISSING
   print(day.record_count)    # derived records produced
   ```

   `as_of`/`calc_ts` are injected, never read from a clock — that is what makes a replay
   reproduce the original bytes.

3. Backfill a date range. Walks the inclusive range ascending, reconstructing each
   stored day; `as_of_for`/`calc_ts_for` map each date to that day's instants.

   ```python
   from orchestration.reconstruction import reconstruct_range

   def as_of_for(d):   return datetime(d.year, d.month, d.day, 15, 30, tzinfo=UTC)
   def calc_ts_for(d): return datetime(d.year, d.month, d.day, 16, 0, tzinfo=UTC)

   report = reconstruct_range(
       store, date(2026, 3, 2), date(2026, 3, 31), positions,
       instruments=instruments, masters=masters,
       config=config, config_hash=config_hash,
       as_of_for=as_of_for, calc_ts_for=calc_ts_for,
   )
   print(report.reconstructed_dates)   # days that produced derived rows
   print(report.missing_dates)         # days with no stored raw partition — holes, not interpolated
   ```

4. Restate a range under a version (leave the live numbers intact). Pass `version="<V>"`
   so each day's restatement lands in its own `version=<V>/` sub-partition beside the
   existing analytic rather than over it.

   ```python
   reconstruct_range(
       store, date(2026, 3, 2), date(2026, 3, 31), positions,
       instruments=instruments, masters=masters,
       config=config, config_hash=new_hash,
       as_of_for=as_of_for, calc_ts_for=calc_ts_for,
       version="2026.06-recalib",        # a stable, meaningful label
   )
   ```

   Read a specific restatement back by the version you wrote it under:

   ```python
   store.list_versions("iv_points", date(2026, 3, 2), "AAPL")
   store.read("iv_points", trade_date=date(2026, 3, 2), underlying="AAPL",
              version="2026.06-recalib")
   ```

5. Verify a replay matches live (drift check). For a day already run live, compare a
   reconstruction's outputs against the persisted live rows, per table, by key and value.

   ```python
   from orchestration.reconstruction import compare_replay_to_live
   comparison = compare_replay_to_live(store, date(2026, 6, 1), day.outputs)
   ```

   Under one code version they must agree on every table; the helper names the table and
   the exact keys if they ever diverge.

## Healthy output

A reconstructed day comes back `status == RECONSTRUCTED` with `record_count > 0`, and the
derived partitions for that date are on disk. A range report lists the days that
reconstructed and, separately, the days that were `MISSING` — a `MISSING` day is a raw
hole reported honestly, never a fabricated empty result. A versioned restatement leaves
`list_versions` showing both the old and the new version. `compare_replay_to_live`
reports agreement on every table.

## When a step fails

- A day comes back `MISSING`: there is no stored raw partition for it. Replay cannot
  invent market data. If the raw data should exist, that is a collection gap — go to the
  [incident-response runbook](incident-response.md); if the market was closed, the
  `MISSING` is correct.
- A day comes back `EMPTY`: the raw partition existed but produced no derived rows (e.g.
  only one-sided quotes). Distinct from `MISSING`; investigate the day's quotes with the
  QC quote-health and chain-coverage checks.
- `compare_replay_to_live` reports a divergence under the *same* code version: that is a
  determinism break — the most serious failure this system can have. Escalate per the
  [incident-response runbook](incident-response.md); the comparison names the table and
  the diverging keys.
- A restatement should not overwrite the live numbers: always pass an explicit `version`
  for a restatement. `version=None` is the live replace-in-place path and *will*
  overwrite that day's derived files.
