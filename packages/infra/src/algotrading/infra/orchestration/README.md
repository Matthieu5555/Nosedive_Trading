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
analytics, EOD reconciliation, QC. Every fire re-runs **all** stages
(overwrite-by-re-run, ADR 0032 refined): each stage's writes are idempotent, so a
killed-and-restarted or re-fired run converges to the same store state; the run-state
ledger records completions for observability and the dashboard backlog, never as a
skip gate. One `correlation_id` threads the whole run (and the actor's own log lines),
so a session resolves to the jobs it fed.

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
  These are **detection only** — pure functions that build an `Alert`; they perform no delivery.
- **alert_delivery** — **THE single alert-delivery seam.** Everything that detects an alert
  routes it here; no other code path opens its own transport. The contract is the `AlertSink`
  port — `deliver(alert, context) -> DeliveryResult` plus a `channel` name. Concrete sinks:
  `WebhookAlertSink` (POSTs the formatted alert via `httpx`) and `JournaldAlertSink` (the honest
  degrade). `resolve_alert_sink()` reads `$HOME/.env` and returns the webhook sink when
  `ALGOTRADING_ALERT_WEBHOOK_URL` is set, else the journald sink — which logs at ERROR (fail-loud
  preserved) and reports `delivered=False, degraded=True`, never a false claim of delivery. A
  webhook transport/HTTP error returns `delivered=False, degraded=False` (surfaced, not swallowed).
  `deliver_alerts(sink, alerts, context)` skips `None`s and returns one `DeliveryResult` per firing.
  **Consumers — including other streams — import `AlertSink`/`resolve_alert_sink`/`deliver_alerts`
  from here; do not fork a second channel.** Call sites take an `alert_sink: AlertSink | None`
  injection seam (e.g. `default_stages_builder`); `None` falls back to `resolve_alert_sink()`.
  Secrets never live in git or a `.py` literal — set `ALGOTRADING_ALERT_WEBHOOK_URL` (and optional
  `ALGOTRADING_ALERT_WEBHOOK_TIMEOUT_SECONDS`) in `$HOME/.env` to go live; absent, it degrades.
- **dashboard** — `build_dashboard`/`render_dashboard`: a pure status object answering
  is-data-flowing / are-surfaces-building / is-QC-passing / are-scenarios-current, with
  the last healthy run and current backlog first-class.
- **run_state** — the durable JSON-lines stage ledger that makes restart idempotent and
  the dashboard answerable. Nothing reads a clock; timestamps are injected.
- **pipeline** — `run_end_of_day`, the ordered/idempotent/logged EOD sequence.
- **eod_runner** — the one-shot the systemd timer fires (WS 1G, ADR 0032), behind
  `scripts/eod_run.py`. The runner is a thin command/application shell — `main()`/`_parse_args`
  and `run_fire` (plan → wire stages → `run_end_of_day` → freeze manifest) — over four cohesive
  pieces it composes:
  - **eod_planning** — the dependency-free leaf: resolves the trade date (default = the injected
    clock's market day; `--trade-date` for catch-up; a *future* date rejected — no look-ahead),
    scopes the fire to a calendar group (`--calendar XEUR` / `--index SX5E`; default = all
    enabled), reads the 1J registry's `enabled_indices()` (never a hardcoded list), and reduces to
    the in-session set, each index paired with its own `session_close` (`plan_fire` → `EodRunPlan`).
  - **eod_dependencies** — the injectable `RunnerDeps` bundle and its production default wiring
    (`build_default_deps`: store, config + hashes, registry, resolver, run repository, stages
    builder, code identity resolved once at the entrypoint).
  - **eod_stages** — the live `default_stages_builder` (capture → analytics(`project_grid` +
    `persist_signal_set`) → persist → reconciliation → QC) and its QC-stage helpers
    (`analytics_qc_results`, `persist_triage`). The analytics stage, after persisting each
    captured index's grid, derives and persists the daily as-of strategy-entry signal set
    (`signals/` — ρ̄ + IV-rank/RV−IV/term-slope) at the index's own session close, so
    `strategy_signals` lands every banked day; its params come from `config.universe.signals`
    (`signal_config_for`). The 1C seam is the *basket source*: until the broker→raw-event bridge
    lands, `_empty_basket_source` returns `None` — a narrow, labeled no-capture gap (clean exit 0),
    not a raise; a credentialed caller injects a live `collect_live`-backed source. On an **intraday
    fire** (`clock.now() < as_of` for any fired index — the manual early-run path) the QC stage still
    **runs in full and persists its verdict** (`qc_results` + triage), so a human can read whether a
    provisional midday capture is genuinely sound — intraday is *not* a free pass. What changes is the
    *consequence*: the escalation is capped below `page` (the runner exits 0, the close pager stays
    silent) and none of the close-incident alerts (qc_fail / coverage / degenerate) fire, because a
    one-sided wing or sparse front-week is expected midday, not a failed close. A genuinely-degenerate
    intraday capture still records `fail` in the report/triage; it simply does not page. The production
    timer fires at/after the close, so a production run is never intraday and its QC — including the
    degenerate-close page — is untouched.
  - **eod_manifest** — freezes the per-run lineage manifest (config snapshot + hashes + code
    identity), recorded for both a clean and a failed fire so each is reproducible from its record.

  `main` exits non-zero on any stage failure so `Restart=on-failure`/`OnFailure=` engage. The
  unit files (`eod-capture@.service`, `eod-capture@{XEUR,XNYS}.timer`, `eod-capture-alert.service`)
  live under `scripts/systemd/`.
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
`test_alert_delivery.py` (the `AlertSink` port contract, the webhook adapter with a mocked
`httpx` transport — formats + targets correctly, a 5xx and a transport error both surface as
`delivered=False` not swallowed — and the no-credentials → journald degrade that says so honestly),
`test_eod_run.py` (WS 1G: the runner builds+invokes `run_end_of_day` with a bound
correlation id and injected clock, idempotent re-fire, missed-day catch-up, mid-run-kill
restart convergence, non-zero failure exit, registry-driven enabled index set, holiday
no-op, future-date rejection, per-run manifest freeze, and the systemd-unit ADR-0032
obligations), and `test_replay_reconstruction.py` for the reconstruction subpackage. The headline
acceptance tests (`test_replay_byte_identical.py`, `test_provenance_verification.py`,
`test_handover_e2e.py`) drive this layer's actor + QC seam.
