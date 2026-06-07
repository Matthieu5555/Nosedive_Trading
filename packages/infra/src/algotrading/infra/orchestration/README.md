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
- **alerts** — named conditions with documented detection intervals on an injected
  clock: collector death, missing partition (named, never interpolated), elevated
  failure rate, QC fail (reuses the QC plane's own escalation rule), and grid coverage
  breach (`coverage_breach_alerts`, WS 1H — one alert per pinned tenor whose grid coverage
  fell below its floor, subject `underlying@tenor`; the orthogonal twin of missing
  partition — *present but too thin* vs *absent* — read off the QC report, not recomputed).
- **dashboard** — `build_dashboard`/`render_dashboard`: a pure status object answering
  is-data-flowing / are-surfaces-building / is-QC-passing / are-scenarios-current, with
  the last healthy run and current backlog first-class.
- **run_state** — the durable JSON-lines stage ledger that makes restart idempotent and
  the dashboard answerable. Nothing reads a clock; timestamps are injected.
- **pipeline** — `run_end_of_day`, the ordered/idempotent/logged EOD sequence.
- **eod_runner** — the one-shot the systemd timer fires (WS 1G, ADR 0032), behind
  `scripts/eod_run.py`. `main()` resolves the trade date (default = the injected clock's market
  day; `--trade-date` for catch-up; a *future* date rejected — no look-ahead), scopes the fire
  to a calendar group (`--calendar XEUR` / `--index SX5E`; default = all enabled), reads the 1J
  registry's `enabled_indices()` (never a hardcoded list), skips a non-session cleanly via the
  calendar resolver, captures each index at its own `session_close`, binds one `correlation_id`,
  calls `run_end_of_day`, and freezes a per-run manifest (config snapshot + hashes + code
  identity). Exits non-zero on any stage failure so `Restart=on-failure`/`OnFailure=` engage. The
  collection stage is the 1C seam — until 1C lands, `default_stages_builder` raises a labeled
  error and a caller injects a replay/fixture `stages_builder` to exercise the timer path. The
  unit files (`eod-capture.service`, `eod-capture@{XEUR,XNYS}.timer`, `eod-capture-alert.service`)
  live under `documentation/connectivity/`.
- **reconstruction/** — historical replay/backfill over a date range; see its own README.

## Gotchas

- **One driver.** Everything routes through `actor.run_analytics`. Do not add a job that
  recomputes analytics another way.
- **Injected clocks only.** Jobs and alerts take a `Clock`/`now`; nothing here reads the
  wall clock, so a replay of the same pipeline reproduces the same ledger and the
  detection-interval tests advance a `ManualClock` instead of waiting.
- **Live collection rides the one unified collector (ADR 0027 / C6).** `collect_live`,
  `surface_job` (`build_surface`) and `provider_flow` (`run_provider_flow`) drive the single
  `collectors.RawCollector` — one `BrokerTick`, content-addressed exactly-once capture, no
  second analytics path. The EOD collection stage is still an *injected* seam on
  `run_end_of_day` (so the sequence stays testable without a broker), and its default wiring is
  `collect_live`. See `jobs.py`'s docstring and ADR 0027.

## Tests

`packages/infra/tests/test_orchestration.py` (behavior, not coverage: kill/restart
idempotency, the five metrics, the alerts — including the WS 1H coverage-breach alert and
its distinctness from missing-partition — dashboard, reconciliation, run-state),
`test_eod_run.py` (WS 1G: the runner builds+invokes `run_end_of_day` with a bound
correlation id and injected clock, idempotent re-fire, missed-day catch-up, mid-run-kill
restart convergence, non-zero failure exit, registry-driven enabled index set, holiday
no-op, future-date rejection, per-run manifest freeze, and the systemd-unit ADR-0032
obligations), and `test_replay_reconstruction.py` for the reconstruction subpackage. The headline
acceptance tests (`test_replay_byte_identical.py`, `test_provenance_verification.py`,
`test_handover_e2e.py`) drive this layer's actor + QC seam.
