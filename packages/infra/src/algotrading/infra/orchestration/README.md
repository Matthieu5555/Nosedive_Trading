# infra.orchestration

The operable layer: it sequences jobs around the **one** actor, records what ran,
measures it, and raises a hand when something is wrong. It holds no math and no
economics — the actor (`algotrading.infra.actor`, hosted on Nautilus per ADR 0023) is
the only analytics driver, and these are jobs *around* it. A second analytics path here
would make the byte-identical-replay guarantee a lie, so there isn't one.

## Fast path — the end-of-day sequence

```python
from algotrading.infra.orchestration import EodStages, run_end_of_day, build_metrics

# each stage is a zero-arg callable closing over store/config/clock/correlation_id
result = run_end_of_day(store, trade_date=day, correlation_id=corr,
                        clock=clock, stages=stages)
```

`run_end_of_day` runs five stages in order — universe refresh, collection, incremental
analytics, EOD reconciliation, QC — skipping any already finished cleanly for the date
(read from the run-state ledger) so a killed-and-restarted run re-does only the
unfinished tail. One `correlation_id` threads the whole run (and the actor's own log
lines), so a session resolves to the jobs it fed.

## What's here

- **jobs** — `refresh_universe`, `run_incremental_analytics` (the actor wrapper that
  times the run and feeds the metrics), `reconcile_end_of_day`, `record_forward_failure`.
- **qc_job** — `run_qc`: runs the QC checks over a day, writes the `QcResult` rows
  (idempotent on the result key), returns the report + one escalation signal.
- **metrics** — five well-labeled prometheus metrics over an injected registry:
  `events_collected_total`, `stale_quote_ratio`, `forward_failures_total`,
  `solver_failures_total`, `scenario_run_seconds`.
- **alerts** — four named conditions with documented detection intervals on an injected
  clock: collector death, missing partition (named, never interpolated), elevated
  failure rate, QC fail (reuses the QC plane's own escalation rule).
- **dashboard** — `build_dashboard`/`render_dashboard`: a pure status object answering
  is-data-flowing / are-surfaces-building / is-QC-passing / are-scenarios-current, with
  the last healthy run and current backlog first-class.
- **run_state** — the durable JSON-lines stage ledger that makes restart idempotent and
  the dashboard answerable. Nothing reads a clock; timestamps are injected.
- **pipeline** — `run_end_of_day`, the ordered/idempotent/logged EOD sequence.
- **reconstruction/** — historical replay/backfill over a date range; see its own README.

## Gotchas

- **One driver.** Everything routes through `actor.run_analytics`. Do not add a job that
  recomputes analytics another way.
- **Injected clocks only.** Jobs and alerts take a `Clock`/`now`; nothing here reads the
  wall clock, so a replay of the same pipeline reproduces the same ledger and the
  detection-interval tests advance a `ManualClock` instead of waiting.
- **Live collection is pending C1.** The `collect_live` job and the EOD collection
  stage's live wiring need the broker-session→`RawMarketEvent` seam C1 has not yet
  reconciled (two `BrokerTick` shapes on the packages stack; owner-deferred). The
  collection stage stays an *injected* seam on `run_end_of_day` — a caller supplies it
  (a fixture replay in tests today; the live job once C1 lands). See `jobs.py`'s
  docstring and ADR 0026.

## Tests

`packages/infra/tests/test_orchestration.py` (behavior, not coverage: kill/restart
idempotency, the five metrics, the four alerts, dashboard, reconciliation, run-state)
and `test_replay_reconstruction.py` for the reconstruction subpackage. The headline
acceptance tests (`test_replay_byte_identical.py`, `test_provenance_verification.py`,
`test_handover_e2e.py`) drive this layer's actor + QC seam.
