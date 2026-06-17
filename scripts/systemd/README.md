# Deploy & operate — the EOD close-capture stack

This is the operating contract for the unattended close-capture deployment. It is the live
replacement for the old `documentation/connectivity/server-deployment-plan.md` (that tree is dead).
Everything below is derived from the units and scripts in this directory and the code they invoke —
not from a plan. When the code changes, this file changes with it.

**One-line mental model:** a per-exchange systemd **timer** fires a one-shot **service** shortly
after each index's close; the service runs `eod_run.py`, which captures that index and banks it. A
failed run routes to an **alert** service. A separate timer **backs up** the store nightly. When
there is no timer (a hand-logged-in box), `eod_babysitter.py` stands in for the timers and also
keeps the gateway session warm.

## Are we ready for tonight's close? (the one command)

    uv run python scripts/eod_healthcheck.py

Run it any time, especially before a Monday pre-close check. It probes the three things a close
needs and exits 0 only if all three are green:

- **gateway authenticated** — through the real `CpRestSession.authenticated()` seam, not a curl. A
  bare `curl https://localhost:5000/` redirecting to a login page does **not** mean the gateway is
  down; this check asks the way production asks.
- **a capture timer armed** — `systemctl --user list-timers` shows an `eod-capture@*` timer with a
  future fire.
- **last capture banked** — the run-state ledger has a fully-healthy trade date, so the previous
  close landed instead of silently failing.

`--json` emits the same verdict as one object. Red checks print the exact remediation command.

There is a **second, close-specific** pre-close probe (run it ~18:00 CET before the Eurex close):

    uv run python -m algotrading.infra_ibkr.preclose_readiness

**Known limitation (verified, intentional):** this 18:00 check currently verifies **auth only**. Its
two-sided-quote-fraction probe is a stub that returns `None`, so the readiness logic reports
`no_quote_observation` and conservatively stays **NOT READY on the quote-health dimension** — it
never fabricates a passing fraction, but it also cannot yet confirm the chain is quoting two-sided
before the close. In practice the verdict reduces to "auth NOT ready" → red, or "auth ready but
quote-health unverified" → still red with reason `no_quote_observation`. A real probe would have to
reproduce most of the capture path (chain discovery + contract qualification + a warmup snapshot via
`cp_rest_close_capture._snapshot_events`), which is a mini-capture with its own failure modes and the
risk of kicking a competing session — out of scope for a hands-off check. **Treat `eod_healthcheck.py`
(auth + timer + last-banked) as the trustworthy gate; treat `preclose_readiness` as auth-confirmation
plus an honest "quote-health not yet verified."** Wiring the real fraction probe is tracked on
`tasks/ibkr-unattended-reauth.md`.

## What fires when

All times are stated in the **exchange** timezone (so the trigger tracks the close across DST, not
the server clock). The `OnCalendar` time is only the *trigger*; the runner resolves the EXACT close
from the exchange calendar, so half-days and holidays are handled by the resolver (a holiday is a
clean no-op), never by editing a timer.

| Unit | Fires | What it does |
|------|-------|--------------|
| `eod-capture@XEUR.timer` | Mon–Fri 22:45 Europe/Berlin | triggers the Eurex-group capture (e.g. SX5E) |
| `eod-capture@XNYS.timer` | Mon–Fri 16:45 America/New_York | triggers the NYSE-group capture |
| `eod-capture@.service` | (on timer fire) | one-shot: `uv run python scripts/eod_run.py --calendar %i` |
| `eod-capture-alert.service` | on a failed capture (`OnFailure=`) | one labeled journald line naming the failed run |
| `data-backup.timer` | Mon–Fri 19:30 Europe/Berlin | triggers the canonical-store backup |
| `data-backup.service` | (on timer fire) | snapshots raw + run-state ledger to `$ALGOTRADING_BACKUP_ROOT` |
| `data-backup-alert.service` | on a failed backup (`OnFailure=`) | one labeled journald line |

`eod-capture@.service` is a **template** (`%i` is the calendar code the matching timer passes).
Adding an index on an already-covered calendar needs **no new unit** — the runner picks it up from
the enabled-index registry. A brand-new exchange calendar adds one timer; generate it with
`uv run python scripts/gen_capture_timers.py` (the timers are generated, not hand-typed — that is
the fix for the past XEUR drift bug).

The timer `OnCalendar` values above are the source of truth; verify them on the box with
`systemctl --user list-timers 'eod-capture@*'`.

## The three CP-gateway session clocks

The IBKR Client Portal gateway has three independent ways a session can lapse. The babysitter
(`scripts/eod_babysitter.py` → `infra_ibkr/babysitter.py`) **self-heals two of them and can only
alarm on the third.** Knowing which is which is the whole point of being woken at 06:00.

| Clock | Symptom (auth/status) | Babysitter response | Operator action |
|-------|-----------------------|---------------------|-----------------|
| **1. Idle timeout** (~minutes) | session still authenticated; just needs a keepalive | **self-heals** — `tickle()` every 60s on the healthy path | none |
| **2. Brokerage-session drop** | `authenticated:true` but `connected/established:false` | **self-heals** — `reauthenticate()`, **no SMS** | none |
| **3. SSO expiry** (~daily) | `authenticated:false` (or a competing session took the line) | **alarms only** — one revive attempt, then a loud `ALARM` (to stdout and journald) | **re-run the SMS login** (below); this is the only one needing a human |

Clock 3 is the ~daily wall. The babysitter does **not** fix it (it cannot — a fresh login needs the
SMS 2FA). It alarms once and stays hands-off so it doesn't kick a competing session off the line.
The permanent fix (unattended OAuth re-auth) is tracked by the `ibkr-unattended-reauth` task; until
that lands, clock 3 is an operator action.

### Operator action for the SSO-expiry alarm

> **Full recovery runbook:** [`RUNBOOK-reauth.md`](RUNBOOK-reauth.md) — the start-to-finish manual
> SMS re-login, the dedicated **second username** (so a backfill never knocks the live feed off the
> line), the pre-close verification, and the **OAuth-enrollment blocker** that keeps this manual.
> The babysitter now also **delivers** this alarm through the C4 seam (an `sso_reauth_needed`
> alert, classified critical) — the push that wakes you IS this event, not just the journald line.

On the server (no GUI), get the gateway ready for data with one command:

    uv run --with selenium python scripts/ibkr_login.py

That checks status, logs in only if needed, opens the brokerage session (`ssodh/init`), and
verifies. If a **SMS challenge** fires, drop the texted code into the watched file in a second
shell:

    printf '<the 6-digit code>' > /tmp/sms_code.txt

(SMS is risk-based, not guaranteed — an idle-but-recent session often re-logs with no SMS at all.)
The low-level browser-only step is `scripts/ibkr_gateway_login.py`; `ibkr_login.py` wraps it plus
the brokerage-session step, so prefer it. After it reports ready, re-run the health check.

## Exit codes (what the runner's exit means)

`scripts/eod_run.py` → `eod_runner.main` maps outcomes to exit codes; the codes are what drive the
systemd `Restart=on-failure` and `OnFailure=` wiring:

| Exit | Meaning | systemd effect |
|------|---------|----------------|
| **0** | clean close (or a no-op holiday, or a notice/clean QC report) | none — success |
| **1** | a stage raised, **or** a critical QC `ESCALATION_PAGE` (data persisted but not trustworthy) | `Restart=on-failure` retries (≤3 in 15 min); then `OnFailure=eod-capture-alert.service` fires |
| **2** | bad request (`EodRunError` — e.g. a future trade date, no look-ahead) | failure; the alert fires |

A QC page is the important subtlety: the pipeline **completed and the data is on disk**, but the
result failed a critical check, so the runner exits 1 on purpose rather than reporting a silent
success. The retry is safe because the run is idempotent (run-state ledger + replace/append store
writes) — a retry re-does only the unfinished tail.

`eod_babysitter.py` follows the same convention end-to-end: it exits non-zero if any planned fire
was missed or any fire returned non-zero, so an unattended run never reports success on a missed or
errored capture.

## When an alert fires — triage

The alert services currently write one labeled line into **journald** (`systemd-cat`). To find the
failed run and its `correlation_id`:

    journalctl --user -u 'eod-capture@*.service' --since today     # the run trace, one id per fire
    journalctl --user -u eod-capture-alert.service --since today   # the alert line
    journalctl --user -u data-backup.service --since today         # backup trace

### Which alert woke you — kind, severity, action

Alerts carry a `kind`; the delivery seam (`alert_delivery.severity_for`) maps each to a severity.
**Critical kinds page** (the push that wakes you); a **warning** is logged/delivered but is not a
"get-up-at-06:00" event. So when you are paged, it is one of the critical kinds below.

| `kind` | Severity | What happened | Operator action |
|--------|----------|---------------|-----------------|
| `degenerate_close` | **critical (pages)** | the close ran but banked nothing usable (no basket, or 0 combined-surface grid cells) — the silent-green gap; the runner exits non-zero | inspect the run by its `correlation_id`; re-run the capture once the gateway/quote-health is restored (a degenerate close is usually a dead session or a market-closed snapshot) |
| `qc_fail` | **critical (pages)** | the QC report escalated to `ESCALATION_PAGE` — data is on disk but failed a critical check | read the triage records for the run; the data persisted but is not trustworthy |
| `sso_reauth_needed` | **critical (pages)** | CP-gateway SSO expired (clock 3) and the babysitter could not revive it | follow [`RUNBOOK-reauth.md`](RUNBOOK-reauth.md) — manual SMS re-login |
| `coverage_breach` | warning (does **not** page) | a tenor's coverage is below floor — partition present but thin | note it; it degrades surface quality but does not block the close. Not the thing that woke you |

The severity split is in `_CRITICAL_KINDS` (`alert_delivery.py`); the alert-kind constants are in
`orchestration/alerts.py`. The exit code the runner returns for a critical-QC/degenerate close is
**1** (see the exit-code table above), which is what fires `OnFailure=`.

The journald line is the **delivery of last resort**. Wiring the alert to a real channel
(Telegram/email/webhook) is owned by the shared alert-delivery seam (the `execution-operational-
hardening` alert sub-lane / board row C4) — swap the alert service's `ExecStart` for that channel
on the server; do **not** fork a second delivery mechanism here.

## Install (per-user, no root)

```sh
loginctl enable-linger "$USER"                       # so user timers fire while logged out
mkdir -p ~/.config/systemd/user
cp scripts/systemd/eod-capture@.service       ~/.config/systemd/user/
cp scripts/systemd/eod-capture-alert.service  ~/.config/systemd/user/
cp scripts/systemd/eod-capture@*.timer        ~/.config/systemd/user/
cp scripts/systemd/data-backup.service        ~/.config/systemd/user/
cp scripts/systemd/data-backup.timer          ~/.config/systemd/user/
cp scripts/systemd/data-backup-alert.service  ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now eod-capture@XEUR.timer eod-capture@XNYS.timer data-backup.timer
```

Credentials and config (`IBKR_USERID`/`IBKR_PASSWORD`, `IBKR_CP_GATEWAY`,
`ALGOTRADING_BACKUP_ROOT`, …) come from the repo-root `.env` — see `.env.example`. The units load it
via `EnvironmentFile=-/srv/project/.env`; the EOD entrypoint also loads it itself. **Set
`$ALGOTRADING_BACKUP_ROOT` to a SECOND location** (external disk / NFS / object-store mount) — a
same-disk backup protects only against a fat-finger, not disk loss, and the backup script refuses to
run with no destination.

## Deployment shape — systemd on the shared box (compose dropped)

The chosen deployment is **per-user systemd on the shared server**, as installed above. The deferred
`docker-compose.yml` + headless `ib-gateway-docker` service is **dropped, on the record** — see
[`.agent/decisions/0055-deploy-via-systemd-compose-dropped.md`](../../.agent/decisions/0055-deploy-via-systemd-compose-dropped.md).
In short: the systemd stack already runs the unattended week and is green; the gateway is driven
headless by `scripts/ibkr_login.py` (no container needed); and the close-capture is a per-close
one-shot, not a long-running service a container would supervise. If a future need (multi-host, a
clean-room gateway image) makes compose worth it, that ADR is the place to reopen it.
