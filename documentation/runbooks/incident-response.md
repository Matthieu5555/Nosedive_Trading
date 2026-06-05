# Incident response

## What this is for

Turn a symptom — an alert, a red dashboard flag, a failed QC check — into the specific
failing object and the right fix. The whole QC plane is built on one rule: a failure
names the exact maturity, quote, underlying, or solver that broke, never a generic red
banner. So incident response here is mostly *reading the name off the failure* and going
to the right place.

## When you run it

Any time an alert fires, a dashboard flag goes bad, end-of-day QC escalates, or a replay
comparison reports a divergence.

## First: pull the triage table

The triage table is the operator's worst-first work queue. It drops the passing rows,
orders fails before warns and critical before warning, and each row's headline names the
offender.

```python
from qc import triage_table, escalation_level
table = triage_table(qc.report)
for row in table.rows:
    print(row.headline)              # names the failing object
print(escalation_level(qc.report))   # none / notice / page
```

`escalation_level` (and the `qc_fail_alert` built on it) is the single definition of the
"alert on QC fails" policy: a critical-severity fail is a `page`, any other fail or any
warn is a `notice`, a clean report is `none`.

## The alert-to-action table

| symptom | what it means | where to go |
|---|---|---|
| `collector_death` alert / `data flowing: no_data` | the session has been silent past `COLLECTOR_SILENCE_SECONDS` (120s); the alert names the session and the silence duration | restart the collector (below); raw is loss-aware so no data is lost on restart |
| `missing_partition` alert / `surfaces building: missing` | an expected `(trade_date, underlying)` analytic partition is absent; the alert names the exact `table date/underlying` | [replay/backfill](replay-backfill.md) that day — never interpolate |
| `elevated_failure_rate` alert | the recent stage runs failed above `MAX_FAILURE_RATIO` (0.5 over a window of 6) — a systemic problem, not noise | inspect `read_stage_runs(store.root)`; find the common failing stage and fix its cause |
| `qc_fail` page | the day's QC escalated to a critical-severity fail | pull the triage table; the top row names the offender; use the check table below |

## The check-to-cause table

Each QC check, what it reads, and what a failure points at. Full detail in
`backend/src/qc/README.md`.

| check | failure names | likely cause |
|---|---|---|
| `check_collector_continuity` | `failing_session` (+ gap count, coverage) | feed dropped or thin; restart the collector |
| `check_underlying_quote_health` | `failing_quote` (instrument key) | a wide or stale underlying quote; the forward built on it is suspect |
| `check_option_chain_coverage` | `underlying` + `missing_contracts` | the chain is incomplete; check the universe refresh |
| `check_forward_stability` | `underlying` + `failing_maturity` | the forward could not be recovered stably for that maturity |
| `check_parity_residual` | `underlying` + `failing_maturity` + worst index | put-call parity broke at a strike; bad quote or bad forward |
| `check_iv_solver_convergence` | `failing_solvers` (contract keys) | the IV solve did not converge for those contracts |
| `check_surface_fit_error` | `underlying` + `failing_maturity` | the slice fit RMSE is too high for that maturity |
| `check_calendar_sanity` | `failing_maturity_short`/`_long` + `failing_k` | total variance is non-monotone across maturities |
| `check_greek_sanity` | `failing_contract` + offending `breaches` | a computed Greek disagrees with the broker's beyond tolerance |
| `check_scenario_completeness` | `missing_cells` (scenario_id, contract_key) | the scenario grid is missing cells |

## Restarting a collector or a job

Restart is safe by construction. The raw layer is append-only and idempotent on a
content-addressed `event_id`, and the end-of-day pipeline skips stages already recorded
clean. So:

- **Collector:** restart it with the *same* `session_id` (derived from the trade date).
  On start it reloads the ids already written and re-feeds only what is new, so a tick
  re-delivered after the restart is written exactly once. An outage becomes an explicit
  gap event, not a silent hole.
- **End-of-day pipeline:** rerun `run_end_of_day` for the same trade date. It reads the
  run-state ledger and resumes from the unfinished tail. Even a re-run stage cannot
  duplicate outputs (the actor replaces partitions in place; writes dedupe on key). Use
  `backlog_stages(store.root, day)` to see what is outstanding and
  `last_healthy_trade_date(store.root)` to find the last fully-clean date.

## Investigating a failed surface build

This is the headline diagnostic walk, and it goes *backwards* from the symptom, because
a surface is the last link in a chain: snapshot → forward → IV points → surface slice.
A surface fails because something upstream did.

1. `check_surface_fit_error` names the failing `underlying` + `maturity`. Start there.
2. Check `check_iv_solver_convergence` for that underlying. If the solves did not
   converge (it names the `failing_solvers`), the slice had too few good IV points to
   fit. The IV solver lives in `backend/src/iv`.
3. If the IVs were fine, check `check_forward_stability` and `check_parity_residual` for
   that maturity. A bad forward poisons every IV solved against it. The forward
   estimator lives in `backend/src/forwards`.
4. If the forward was fine, check `check_underlying_quote_health` and
   `check_option_chain_coverage`. A stale or wide underlying quote, or a chain missing
   contracts, starves the forward and the fit. Snapshots live in `backend/src/snapshots`.
5. If the inputs were all there and good but the fit still failed, the issue is in the
   fit itself — `backend/src/surfaces`, the `SliceFit` for that maturity. The actor that
   wires snapshot → forward → IV → surface is `backend/src/actor`.

At each step the QC result's context payload names the specific object, so you are
following named offenders down the chain, not guessing.

## When the failure is determinism

If `compare_replay_to_live` reports a divergence under the *same* code version, or the
byte-identical replay test (`tests/test_replay_byte_identical.py`) fails, this is the
most serious class of failure the system has: the same inputs produced different
outputs. The comparison names the table and the diverging keys. Do not paper over it —
this breaks the four invariants the platform exists to guarantee (see
[known limitations](../known-limitations.md) and `BIG_PICTURE.md`). Escalate to a code
owner; it almost always means something read a clock, a hash, or an unstable ordering it
should not have.

## Who to contact

See [known limitations](../known-limitations.md), "Support model", for the contact and
escalation path.
