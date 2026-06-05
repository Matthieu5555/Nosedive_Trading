# orchestration — jobs, metrics, alerts, dashboard, and the end-of-day sequence

This package is the operations layer. The actor (the compute keystone) and the QC
plane already do the math; this turns them into something an operator runs and
watches. It holds no economics of its own — it sequences the jobs, records what ran,
measures it, and raises a hand when something is wrong. Think of it as the kitchen
manager: the cooks (actor, QC) are already good, this is the person who runs the pass,
keeps the tickets in order, and shouts when a station goes dark.

## TL;DR — run the end-of-day sequence

The one entrypoint is `run_end_of_day`. It runs five stages in order — universe
refresh, collection, incremental analytics, EOD reconciliation, QC — records each
clean completion, and on a restart skips whatever already finished. You hand it the
five stages as zero-argument callables that close over your store, config, clock, and
correlation id:

    from orchestration import EodStages, run_end_of_day

    stages = EodStages(
        universe_refresh=lambda: refresh_universe(...),
        collection=lambda: collect_live(...),
        analytics=lambda: run_incremental_analytics(...),
        reconciliation=lambda: reconcile_end_of_day(...),
        qc=lambda: run_qc(...),
    )
    result = run_end_of_day(store, trade_date=day, correlation_id=corr,
                            clock=clock, stages=stages)

`result.ran` and `result.skipped` tell you which stages this attempt did. Every stage
is logged under the one `correlation_id`, so the whole run resolves as one trace. The
clock is injected (use `connectivity.SystemClock` in production, `ManualClock` in
tests) — nothing in this package reads a wall clock, which is what keeps the recorded
timestamps reproducible.

## The jobs

Each job is a plain function of injected dependencies with a structured,
correlation-id-bound log line. None of them schedules itself; call them directly (the
pipeline does, and so do the tests).

`refresh_universe` persists the day's instrument masters (resolved by B, passed in) to
the append-only master table — idempotent on the instrument key. `collect_live` drives
B's `MarketDataCollector` over one supervised session and bumps the event counter per
underlying; the `session_id` is the correlation handle and is stable across restarts.
`run_incremental_analytics` replays the day's raw events through `actor.run_analytics`
(one code path with live), times the run, derives the stale-quote and solver-failure
metrics, and persists with the actor's replace-semantics. `reconcile_end_of_day` runs
D's `risk.reconcile` of each computed line against its broker Greek row and returns the
named breaches. `run_qc` runs the QC checks over the day's outputs, writes the
`QcResult` rows, and returns the report plus its escalation level.

## Building a volatility surface (`surface_job.py`)

`build_surface` is the reusable "give me a surface for this symbol" use case — the home
of the workflow the `scripts/vol_surface.py` operator script used to inline. It composes
the existing jobs: resolve and materialize the bounded option chain off an injected
broker session, `collect_live` a window of quotes, `assess_market_data` the feed's
entitlement/health from the session's diagnostics and the collection counts, then
`run_incremental_analytics` (empty book — a surface needs no positions) over the
freshly-collected raw events, and reduce the persisted SVI parameters to
`SurfaceSliceSummary` rows. It returns a `SurfaceJobResult` (outputs, collection summary,
`MarketDataStatus`, and the per-maturity summaries) under one `correlation_id`. The chain
*policy* is `universe.chain_planning`, the surface math is `surfaces`, and entitlement
diagnostics come from an optional `MarketDataDiagnostics` source (the live IBKR adapter
supplies them; a fake or replay session does not). For a live run leave the request's
`as_of`/`calc_ts` unset and the job stamps them from the clock *after* collection, so the
snapshot never values as-of a time before the quotes it read.

## Metrics

Five metrics, built over an injected `prometheus_client` registry by `build_metrics`.
The design rule is fewer well-labeled metrics over many opaque ones, so every metric
carries a label naming *which* underlying or job it is about.

| metric | type | labels | what it measures |
|---|---|---|---|
| `events_collected_total` | counter | underlying | observations a session persisted; the event rate is its delta over time |
| `stale_quote_ratio` | gauge | underlying | fraction of a snapshot batch whose quotes were not usable (0..1) |
| `forward_failures_total` | counter | underlying | forwards that could not be recovered for a maturity |
| `solver_failures_total` | counter | underlying | IV solves that did not converge |
| `scenario_run_seconds` | histogram | job | wall time of one analytics/scenario run |

Read a current value with `sample_value(registry, name, labels)`; it resolves the
`_total` suffix for counters and returns `0.0` for an un-incremented metric.

## Alerts

Four named conditions, each a pure function of recorded state plus an injected `now`.
The "detection interval" is the bound within which the layer promises to notice the
condition — it is the contract the timing tests pin, and it is the number an operator
reads off the alert. The intervals live at the top of `alerts.py`.

| alert | fires when | detection interval |
|---|---|---|
| `collector_death` | no observation within the silence window of the last heartbeat | `COLLECTOR_SILENCE_SECONDS` (120s) |
| `missing_partition` | an expected `(trade_date, underlying)` analytic partition is absent | immediate — named, never interpolated |
| `elevated_failure_rate` | the share of failed stage runs over the recent window exceeds the threshold | next evaluation once the window fills (`FAILURE_WINDOW`=6, ratio > `MAX_FAILURE_RATIO`=0.5) |
| `qc_fail` | the day's QC report escalates to `page` (a critical-severity fail) | the moment the report is evaluated |

`collector_death` is detected within its interval *of the last heartbeat*: advance an
injected clock to `last_event + 120s` and it fires; one second short and it does not.
That is the whole reason the clock is injected — the "detected within N" test needs no
real wait.

## Dashboard

`build_dashboard` returns a `DashboardStatus` from recorded state — no side effects.
It answers four flags (is data flowing, are surfaces building, is QC passing, are
scenario reports current) and carries the two operational facts first-class: the last
fully-healthy trade date and the current backlog (the stages not yet finished cleanly
for the date). `render_dashboard` turns it into a compact plain-text panel; the
last-healthy and backlog lines lead, because those are what an operator looks at first.

## Restart procedure

Restart is safe by construction, and the recorded state makes it cheap. The run-state
ledger (`run_state.py`, a JSON-lines file under the store root, written atomically)
records each stage that finished cleanly. On restart, `run_end_of_day` reads the
ledger and skips any stage already clean for the date, so a pipeline killed mid-run
re-does only the unfinished tail. Even a stage that *does* re-run cannot duplicate or
corrupt outputs: the actor replaces derived partitions in place, the collector and
master writes dedupe on key, and the ledger append is atomic. So the procedure is
simply: rerun `run_end_of_day` for the same trade date. To inspect before rerunning,
`backlog_stages(root, date)` lists what is outstanding and `last_healthy_trade_date(root)`
names the last date whose full sequence finished clean.

A stage that *ran but did not pass* — reconciliation with a breach, QC that did not
pass `pass` — records a `failed` outcome, which counts as backlog, not as healthy, so a
fixed input gets a clean rerun rather than being silently treated as done.

## What this package does not do

It does no math, reads no clock, and imports no broker SDK. The `reconstruction`
subpackage (historical replay and backfill) is owned separately and is deliberately not
imported here.
