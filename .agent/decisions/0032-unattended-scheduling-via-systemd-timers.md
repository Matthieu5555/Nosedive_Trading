# 0032 — Unattended scheduling via systemd timers, not an orchestration platform

- **Status:** accepted, 2026-06-06.
- **Date:** 2026-06-06.
- **Implements:** roadmap **1G** (daily close-capture cron) — the roadmap now lives in `TARGET.md`
  (`documentation/roadmap-index-analytics.md` was removed with the `documentation/` tree).
- **Relates to:** [[0026-orchestration-observability-reconciliation]] (the run-state ledger + EOD
  pipeline this triggers), [[0023-nautilus-runtime-spine-and-library-leverage]].

## Context

The orchestration layer already provides an **idempotent** `run_end_of_day()` and a **run-state
ledger** ([[0026-orchestration-observability-reconciliation]]). What 1G needs is only a way to
*trigger* that one job once a day, unattended, on a single headless server, with retry, missed-run
catch-up, and visibility into failures. There is **no multi-task DAG** here.

A web-sourced audit (deep-research, 2026-06-06) compared cron, systemd timers, APScheduler, and the
orchestration platforms (Prefect, Dagster, Airflow, Temporal). Two facts were load-bearing:
APScheduler is an **in-process library, not a daemon** (it needs a resident supervised process to do
what a timer does for free); and the orchestration platforms each stand up a server + metadata DB
(often a broker/UI) — a multi-process deployment to trigger one function.

## Decision

1. **Schedule the daily close-capture with a systemd timer + a `oneshot` service** wrapping
   `run_end_of_day()`: `OnCalendar` at the EOD time, **`Persistent=true`** to catch a run missed while
   the box was down, **`Restart=on-failure` + `RestartSec=`** for retry, **`OnFailure=`** routed to a
   small alert unit, and **journald** for queryable run history. Idempotency stays in the existing
   ledger.
2. **Plain cron + a thin Python runner** is an acceptable equivalent where systemd is unavailable; you
   then re-implement logging, retry, and missed-run handling that the timer gives for free.
3. **Do not adopt APScheduler** (resident supervised process for what a timer does natively, weaker
   missed-run/observability) **or any orchestration platform** for one daily job.

## Consequences

- **Zero new Python dependencies.** The ops artifacts (unit files) live in `scripts/systemd/`
  (`documentation/connectivity/` was removed with the `documentation/` tree).
- Coheres with [[0031-ibkr-historical-data-cp-rest-oauth1a]]: the timer fires the daily job while the
  OAuth 1.0a session/tickler keeps the IBKR REST session alive — no competing standing scheduler.

## Graduation trigger

Adopt an orchestration platform **only when this stops being one independent daily job and becomes a
graph of interdependent tasks/backfills/distributed workers needing a shared run UI** — Prefect for
fastest time-to-value at that threshold, Dagster if asset lineage/governance is the driver. Until that
DAG materialises, a timer is correct.

## Alternatives considered (rejected)

- **APScheduler** — in-process; adds a permanently-running supervised process just to trigger one job,
  with weaker missed-run and observability guarantees than a timer.
- **Prefect / Dagster / Airflow / Temporal** — each requires a standing server + metadata DB (and
  often a broker/worker/UI) to schedule a single function; overkill, and a real operational + lock-in
  cost for no benefit at this scale.
