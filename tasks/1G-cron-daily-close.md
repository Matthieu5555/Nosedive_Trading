# 1G — Daily cron: unattended close-capture via a systemd timer

- **Owns:** the EOD runner entrypoint `scripts/eod_run.py` (builds the default `EodStages`
  wiring + binds a per-day `correlation_id` and trade date, then calls `run_end_of_day`), and the
  ops artifacts — a systemd `oneshot` service, a daily timer, and a small `OnFailure=` alert unit —
  authored under `documentation/connectivity/` next to the rest of the server-deploy plumbing.
  Conforms to **[ADR 0032](../.agent/decisions/0032-unattended-scheduling-via-systemd-timers.md)**
  and roadmap **1G** in `documentation/roadmap-index-analytics.md`.
- **Depends on:** **1C** (the live close-capture mode the timer triggers — `collect_live` is the
  `collection` stage `run_end_of_day` runs; until 1C closes the broker→raw-event seam the runner
  wires a replay/fixture collection stage so the timer path is exercisable). ADR 0032 (accepted).
  The IBKR OAuth 1.0a session/tickler ([ADR 0031](../.agent/decisions/0031-ibkr-historical-data-cp-rest-oauth1a.md))
  keeps the REST session alive under the timer — no competing standing scheduler.
- **Blocks:** nothing structurally. It is the unattended-operation gate for Phase 1 — history only
  accrues without a human if this fires daily.
- **State going in (verified 2026-06-07):** `run_end_of_day()` in
  `packages/infra/src/algotrading/infra/orchestration/pipeline.py` is a **one-shot callable** with
  injected `EodStages`; the run-state ledger (`orchestration/run_state.py`) is an idempotent,
  append-only JSONL record (`completed_stages`/`backlog_stages`/`last_healthy_trade_date`); the
  stage jobs (`orchestration/jobs.py`) and `store_root` exist and are exported. **Nothing schedules
  or daemonises the call** — there is no `scripts/eod_run.py`, no systemd unit, no cron entry.
  APScheduler is not present and would be in-process anyway (ADR 0032). The connectivity guides
  (`documentation/connectivity/{README,server-deployment-plan,capture-forward,connect-providers}.md`)
  already carry **gated** "1C/1G cron — not built yet" notes left intact by the in-flight
  server-deploy-plumbing task (`tasks/server-deploy-plumbing.md`).

## Objective

A daily, unattended trigger fires `run_end_of_day()` once per market day on a single headless
server, with retry, missed-run catch-up while the box was down, and queryable failure history —
implemented as a **systemd timer + `oneshot` service**, not APScheduler and not an orchestration
platform. Idempotency and gap-tracking stay entirely in the existing run-state ledger: a re-fire,
a catch-up fire, and a mid-run-kill restart all converge to the same ledger and the same store
state. The ledger shows idempotent, gap-free runs across market days.

## What to do (ordered)

1. **Build the runner entrypoint `scripts/eod_run.py`.** A thin, dependency-light `main()` that:
   resolves the trade date (default = the clock's current market day; accept `--trade-date` for a
   catch-up/backfill fire), builds a `ParquetStore` at the configured root, binds **one**
   `correlation_id` for the run (e.g. a UUID; record it in the log line so journald and the ledger
   share it), constructs the default `EodStages` wiring (close over store/config/clock/`correlation_id`;
   the `collection` stage is `collect_live` once 1C lands, a replay stage until then), and calls
   `run_end_of_day(store, trade_date=…, correlation_id=…, clock=…, stages=…)`. Exit non-zero on any
   stage raising so `Restart=on-failure` and `OnFailure=` engage. Run under `uv` (no new dependency —
   ADR 0032 §Consequences: zero new Python deps). **Each fire emits its per-run manifest** — the
   ADR 0028 / C7 provenance freeze (resolved config + per-bundle `config_hashes` + code identity =
   commit SHA + dirty flag), which is `run_end_of_day`'s existing step. The cron must not bypass it: a
   scheduled run must be reproducible **from its manifest**, not merely traceable through the JSONL ledger.
2. **Authoring the systemd `oneshot` service** under `documentation/connectivity/` (e.g.
   `eod-capture.service`): `Type=oneshot`, `ExecStart=` invoking `uv run python scripts/eod_run.py`
   in the repo, `Restart=on-failure` + `RestartSec=` for retry, `OnFailure=eod-capture-alert.service`,
   journald for stdout/stderr. Do **not** put the schedule in the service.
3. **Author the daily timer** (`eod-capture.timer`): `OnCalendar=` at the EOD time (state the
   timezone explicitly — exchange close, not server local-by-accident), **`Persistent=true`** so a
   run missed while the box was down fires on next boot, and the catch-up fire reconstructs the
   gap day through the ledger.
4. **Author the alert unit** (`eod-capture-alert.service`): a small `oneshot` the `OnFailure=`
   routes to — a single notification of the failed run + its `correlation_id` so the operator can
   `journalctl` the trace. Keep it minimal; no new dependency.
5. **Document install/operate** in the existing connectivity guides — flip the gated "1C/1G cron —
   not built yet" notes to the real unit names + `systemctl --user enable --now eod-capture.timer`
   and the `journalctl -u eod-capture.service` / ledger-query recipe. **Coordinate with
   `tasks/server-deploy-plumbing.md`** (it owns those connectivity files); extend, do not duplicate
   the connect/bootstrap guides. Record ADR 0032's **graduation trigger** inline (move off the timer
   only when this becomes a DAG of interdependent tasks needing a shared run UI).

## Test surface

Read `tasks/TESTING.md`. The unit files and timer are ops artifacts (held to behavior, not a
coverage number — TESTING.md "transport and orchestration tiers"); the **runner** carries the
asserted cases. Name these tests (in `packages/infra/tests/` for the runner; co-located for any
artifact lint):

- **`test_eod_run_builds_and_invokes`** — `main()` on a tmp store root calls `run_end_of_day` with
  a bound `correlation_id`, the resolved trade date, an injected clock, and a full `EodStages`; no
  wall-clock read sneaks in (clock injected, same discipline as the ledger).
- **`test_eod_run_idempotent_refire`** — fire the runner twice for the same trade date against one
  store root; the second fire **skips** every already-clean stage (ledger `completed_stages`
  unchanged in count of clean stages; no duplicate/corrupt store output). Expected outcome derived
  independently from the ledger semantics in `run_state.py`, not from the runner's own return.
- **`test_eod_run_missed_day_catchup`** — fire for trade date D-2, then D (skip D-1), then for D-1;
  assert `last_healthy_trade_date` and `backlog_stages` show D-1 filled and **no gap** remains —
  the Persistent=true catch-up semantics proven at the ledger the timer drives.
- **`test_eod_run_midrun_kill_restart_converges`** — inject a stage that raises (the documented
  pattern: pass an `EodStages` whose one callable raises), assert `main()` exits non-zero and the
  failed stage is **not** recorded; re-fire with a clean stage set and assert the run completes and
  the ledger is gap-free for the date (restart-convergence, TESTING.md determinism).
- **`test_eod_run_failure_exit_code`** — a raising stage yields a non-zero process exit (subprocess
  or `SystemExit` assertion) so `Restart=on-failure`/`OnFailure=` actually trigger.
- **Edge cases** (TESTING.md floor): empty ledger / first-ever run; a future `--trade-date`
  rejected with a labeled error (no look-ahead — never capture a day that has not closed); a
  trade date whose stages are all already clean is a clean no-op, not a re-run.
- **Artifact sanity** — a test asserting the committed unit files carry `Persistent=true`,
  `Restart=on-failure`, `OnFailure=`, `Type=oneshot`, and an explicit-timezone `OnCalendar=`
  (the ADR 0032 obligations), so an edit that drops one goes red.

## Done criteria

`scripts/eod_run.py` builds the default wiring and invokes `run_end_of_day` with an injected clock
and a bound `correlation_id`; the systemd `oneshot` service + daily timer + alert unit are
committed under `documentation/connectivity/` with `OnCalendar`/`Persistent=true`/`Restart=on-failure`/
`OnFailure=`/journald per ADR 0032; the connectivity guides document install + operate (gated notes
flipped, no duplication of server-deploy-plumbing's content); the named runner tests pass, proving a
re-fire, a missed-day catch-up, and a mid-run-kill restart all leave the run-state ledger
idempotent and **gap-free**; the graduation trigger is recorded; root gate green
(`ruff && mypy && lint-imports && pytest`).

## Gotchas

- **No APScheduler, no Prefect/Dagster/Airflow/Temporal** for one daily job (ADR 0032 §Decision §3).
  The timer is the scheduler; the runner stays a one-shot. Adding an in-process scheduler
  reintroduces the resident-supervised-process problem the ADR rejected.
- **Idempotency is not the runner's to invent** — it lives in the ledger + the replace-/append-idempotent
  store writes. The runner must not add its own dedupe; it just binds the id and calls through, so a
  re-fire and a catch-up are safe by construction.
- **No look-ahead / no future capture.** The trade date must be a day that has *closed*; reject a
  future `--trade-date`. The timer's `OnCalendar` fires *after* the exchange close — state the
  timezone explicitly so it is the exchange close, not server-local by accident, and never captures
  a still-open session.
- **One `correlation_id` per fire**, flowed into the log so journald and the ledger resolve the same
  trace end to end (pipeline already binds it through every stage).
- **Coordinate file ownership** with the in-flight `tasks/server-deploy-plumbing.md`: it owns
  `documentation/connectivity/**`. Author the unit files and flip the gated 1G notes there in step
  with it — do not re-author the connect/bootstrap guides it already owns.
- **The collection stage is the 1C seam.** Until 1C closes the broker→raw-event bridge, wire a
  replay/fixture collection stage so the timer path is fully exercisable today (the pipeline already
  injects stages for exactly this reason); swap to `collect_live` when 1C lands — do not block the
  timer on it.
- **`uv` only**, zero new dependencies (ADR 0032 explicitly counts "zero new Python dependencies" as
  a consequence of choosing the timer).
