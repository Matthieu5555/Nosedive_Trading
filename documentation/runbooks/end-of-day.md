# End of day

## What this is for

Run the canonical end-of-day sequence: refresh the universe, finish collection, run the
day's analytics, reconcile against the broker's Greeks, and run QC. This is the one
operation that turns a day's raw captures into the full set of derived analytics and a
pass/warn/fail verdict, recorded so you can prove tomorrow what ran today.

## When you run it

Once, after the session closes. Also run it to resume a day that was interrupted — the
sequence is restartable and skips whatever already finished cleanly (see Restart below).

## Steps

Everything runs from `backend/`. The one entrypoint is `run_end_of_day`. You hand it the
five stages as zero-argument callables that close over your store, config, clock, and
correlation id; the pipeline runs them in order, records each clean completion, and on a
restart skips whatever already finished.

```python
from datetime import date
from connectivity import SystemClock
from orchestration import EodStages, run_end_of_day
from storage import ParquetStore

store = ParquetStore("<data-root>")
day = date(2026, 6, 1)
corr = "eod-2026-06-01"            # the correlation id that ties the whole run together

stages = EodStages(
    universe_refresh=lambda: refresh_universe(...),       # orchestration.refresh_universe
    collection=lambda: collect_live(...),                 # orchestration.collect_live
    analytics=lambda: run_incremental_analytics(...),     # orchestration.run_incremental_analytics
    reconciliation=lambda: reconcile_end_of_day(...),     # orchestration.reconcile_end_of_day
    qc=lambda: run_qc(...),                               # orchestration.run_qc
)
result = run_end_of_day(store, trade_date=day, correlation_id=corr,
                        clock=SystemClock(), stages=stages)

print(result.ran)         # stages this attempt ran
print(result.skipped)     # stages already clean from an earlier attempt
print(result.escalation)  # the QC escalation level if QC ran: none / notice / page
```

The five stages, in order:

1. **universe refresh** — persist the day's instrument masters append-only (idempotent
   on the instrument key).
2. **collection** — drive the collector over the supervised session and count events
   per underlying.
3. **analytics** — replay the day's raw events through `actor.run_analytics` (the same
   code path as live) and persist the derived outputs with replace-semantics.
4. **reconciliation** — run `risk.reconcile` of each computed line against its broker
   Greek row; a breach records the stage as `failed`, not `ok`.
5. **QC** — run the checks over the day's outputs, write the `QcResult` rows, return the
   report and its escalation level.

To read the QC report on its own (the new-engineer "read the QC report" step), the QC
job is callable directly:

```python
from datetime import datetime, UTC
from qc import thresholds_from_config
from orchestration import run_qc
from collectors import summarize_session

thresholds = thresholds_from_config(config.qc_threshold)
summary = summarize_session(
    events, session_id="2026-06-01", trade_date=day,
    subscribed_keys=subscribed, reconnect_count=0,
)
qc = run_qc(
    store=store, thresholds=thresholds, collector_summary=summary,
    trade_date=day, run_id="qc-2026-06-01", run_ts=datetime.now(UTC),
    correlation_id=corr, persist=True,
)
print(qc.overall_status)   # pass / warn / fail
print(qc.escalation)       # none / notice / page
```

`qc.results` are the `QcResult` rows (also written to the `qc_results` table); each
failing row names the specific offending maturity, quote, underlying, or solver in its
context payload. Roll them through `qc.report` and `triage_table` for the worst-first
work queue.

`run_qc` itself only runs `check_collector_continuity` from the collector summary; the
other nine checks read C/D objects the caller already has in hand (forwards, IV results,
slice fits, risk lines). Build those `QcResult` rows with the matching `qc.check_*`
functions and pass them in via `extra_results=` so `run_qc` stays the single place that
rolls the report, persists the rows, and computes escalation. See the
[QC README](../../backend/src/qc/README.md) for the ten checks and their inputs.

## Healthy output

`result.ran` lists every stage that needed to run and `result.skipped` the rest;
`result.escalation` is `none`. Reconciliation is clean (no breaches). The QC report's
`overall_status` is `pass`. The `qc_results`, `market_state_snapshots`, `forward_curve`,
`iv_points`, `surface_parameters`, `pricing_results`, `risk_aggregates`, and
`scenario_results` partitions for the date are present on disk.

## Restart

The sequence is safe to rerun. A run-state ledger under the store root records each
stage that finished cleanly; on restart `run_end_of_day` reads it and skips the
already-clean stages, so a pipeline killed mid-run re-does only the unfinished tail. Even
a stage that *does* re-run cannot duplicate or corrupt outputs — the actor replaces
derived partitions in place, the collector and master writes dedupe on key, and the
ledger append is atomic. So the restart procedure is simply: rerun `run_end_of_day` for
the same trade date.

To inspect before rerunning:

```python
from orchestration import backlog_stages, last_healthy_trade_date
backlog_stages(store.root, day)          # the stages still outstanding for the date
last_healthy_trade_date(store.root)      # the last date whose full sequence finished clean
```

A stage that *ran but did not pass* (a reconciliation breach, a QC that did not reach
`pass`) records a `failed` outcome and counts as backlog, not as healthy — so after you
fix the input it gets a clean rerun rather than being silently treated as done.

## When a step fails

- A stage callable raises: the run stops there, that stage's completion is *not*
  recorded (so it is backlog on restart), and the store is left consistent. Fix the
  cause and rerun `run_end_of_day` — it resumes from the failed stage.
- Reconciliation reports breaches: a computed Greek disagrees with the broker's beyond
  tolerance. The result names the failing contract and the offending Greek. See the
  [incident-response runbook](incident-response.md), "Greek sanity" row.
- QC escalates to `page`: a critical-severity check failed. Pull `triage_table(qc.report)`
  — the top row names the offender — and follow the
  [incident-response runbook](incident-response.md).
- A derived partition is missing for an underlying that had raw data: the analytics
  stage did not produce it. Fill it with [replay/backfill](replay-backfill.md); a
  missing partition is never interpolated.
