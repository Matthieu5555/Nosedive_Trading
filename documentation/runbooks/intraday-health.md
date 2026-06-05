# Intraday health

## What this is for

Answer four questions at a glance while the day is live: is data flowing, are surfaces
building, are QC checks passing, are the scenario reports current. The dashboard exists
so you read those answers off recorded state in seconds instead of reconstructing them
from logs, and so the last healthy run and the current backlog are always one look away.

## When you run it

Periodically through the session, and any time an alert fires. This is a read-only
check — it has no side effects and reproduces the same status for the same state.

## Steps

Everything runs from `backend/`.

1. Build and render the dashboard for the current trade date. It is a pure read over
   the partitions on disk, the run-state ledger, the latest QC verdict, and the live
   metrics registry.

   ```python
   from datetime import date
   from orchestration import build_dashboard, render_dashboard, build_metrics
   from storage import ParquetStore
   import prometheus_client

   store = ParquetStore("<data-root>")
   metrics = build_metrics(prometheus_client.CollectorRegistry())
   status = build_dashboard(
       root_partitions=store.list_partitions("market_state_snapshots"),
       surface_partitions=store.list_partitions("surface_parameters"),
       scenario_partitions=store.list_partitions("scenario_results"),
       trade_date=date(2026, 6, 1),
       qc_status="passing",            # latest QC verdict for the date
       metrics=metrics,
       ledger_root=store.root,
   )
   print(render_dashboard(status))
   ```

   The panel leads with the two operational facts — `last healthy run` and `backlog` —
   then the four flags. See `backend/src/orchestration/README.md` for the dashboard's
   semantics.

2. Read the metrics. Five well-labeled metrics, each carrying the underlying or job it
   is about (the design rule is few labeled metrics, not many opaque ones):
   `events_collected_total`, `stale_quote_ratio`, `forward_failures_total`,
   `solver_failures_total`, `scenario_run_seconds`. Read a current value with
   `sample_value`:

   ```python
   from orchestration import sample_value
   sample_value(metrics.registry, "stale_quote_ratio", {"underlying": "AAPL"})
   ```

3. Evaluate the alert conditions. Four named conditions, each a pure function of
   recorded state plus an injected `now`. The detection interval is the bound within
   which the layer promises to notice the condition.

   ```python
   from datetime import datetime, UTC
   from orchestration import (
       collector_death_alert, elevated_failure_rate_alert,
       missing_partition_alerts, qc_fail_alert, read_stage_runs,
   )

   now = datetime.now(UTC)
   death = collector_death_alert(
       session_id="2026-06-01", last_event_ts=last_seen, now=now,
   )                                    # fires at last_event + 120s of silence
   gaps = missing_partition_alerts(
       table="surface_parameters", expected=expected_pairs,
       present=store.list_partitions("surface_parameters"),
   )                                    # one alert per absent (date, underlying)
   rate = elevated_failure_rate_alert(runs=read_stage_runs(store.root))
   page = qc_fail_alert(qc_report)      # fires when QC escalates to page
   ```

## Healthy output

The rendered panel shows `overall: healthy` — `data flowing: ok`,
`surfaces building: ok`, `qc: passing`, `scenarios current: current`. `backlog` is
`none`. `collector_death_alert` returns `None`; `missing_partition_alerts` returns an
empty list; `stale_quote_ratio` is low and `forward_failures_total` /
`solver_failures_total` are not climbing.

## When a step fails

- `data flowing: no_data` or a fired `collector_death` alert: the collector is silent.
  The alert names the session and how long it has been silent (it fires at 120s of
  silence by default — `COLLECTOR_SILENCE_SECONDS`). Go to the
  [incident-response runbook](incident-response.md), "collector death" row, and follow
  the restart procedure.
- `surfaces building: missing` or a `missing_partition` alert: an expected analytic
  partition is absent. The alert names the exact `table trade_date/underlying`. A
  missing partition is never interpolated — it is a hole to fill by
  [replay/backfill](replay-backfill.md), not to paper over.
- `qc: failing` or a `qc_fail` page: the day's QC escalated to a critical fail. Pull the
  triage table (it names the offender) and follow the
  [incident-response runbook](incident-response.md).
- A climbing `forward_failures_total` or `solver_failures_total` for one underlying:
  that underlying's analytics are degrading. This is where a failed surface build shows
  up first; the [incident-response runbook](incident-response.md) has the
  walk-back-the-chain procedure.
