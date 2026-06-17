# platform-capture-alert-wiring — a failed/degenerate close must page, not exit 0 silently

**Owner:** Matthieu · **Lane:** `platform-` · **Priority:** P0 (before trusting the unattended week)

> **⚠️ STATUS UPDATE (2026-06-17 board audit) — the premise below is now PARTLY STALE.** Commit
> `3788d34` landed most of the wiring this spec asked for:
> - `qc_fail_alert` now **has a production caller** — `infra/orchestration/eod_stages.py:347` calls
>   `qc_fail_alert(job.report)` (the "no production caller exists" claim is no longer true).
> - A QC `critical` page now **exits non-zero** — `eod_runner.py:152-154` maps `ESCALATION_PAGE` to
>   `return 1`, engaging systemd `OnFailure=` (no more silent exit-0).
> - A closed-market / zero-options close no longer silently banks — `cp_rest_close_capture.py:586-685`
>   lands rows / raises `CloseCaptureError` (a true `result is None` no-op still exits 0 by design).
>
> **Two acceptance items REMAIN OPEN** (this is what the spec is now scoped to):
> 1. **No real delivery channel.** `qc_fail_alert` output still only goes to `log.error`
>    (`eod_stages.py:349-354`); the systemd `*-alert.service` units `systemd-cat` into journald with a
>    "swap for a real channel" TODO. No Telegram/email/webhook delivery exists anywhere.
> 2. **No pre-close (~18:00) readiness check** (gateway-authed + two-sided fraction healthy *before*
>    the close). Still absent.

## Why (audited 2026-06-15)

The capture chain has loud *detection* but no *routing*: several real failure modes leave the run
indistinguishable from success in systemd. The audit found:

- **Quote-integrity / closed-market no-op** — if the 18:15 snapshot two-sided fraction is below
  the floor (`cp_rest_close_capture.py:677-685`, threshold `platform_config.py:559`), the basket is
  `None` → analytics writes nothing → the run **exits 0 with no data banked and no alert**. This is
  exactly the original canary failure; today it is silent.
- **QC `critical` fail** — `run_qc` never raises; the pipeline records `OUTCOME_FAILED` but the run
  banks the analytics and the close reads as "done". `alerts.py:172-188` defines `qc_fail_alert` but
  **no production caller exists** (matches [[deferred-disconnect-alert]]).

## Scope

Wire the existing alert plumbing (`infra/orchestration/alerts.py`, the systemd `OnFailure=
eod-capture-alert.service`) to fire on: (a) a `None`/empty basket / zero-options-banked close, (b) a
QC `critical` page, (c) the gateway-disconnect ALARM already detected ([[deferred-disconnect-alert]]).
Deliver via Telegram/email (pick one channel; the deferred memory has the bring-up). A banked-but-
QC-failed close should alert AND be visibly marked (not a silent green).

## Acceptance

- A forced closed-market / zero-options run sends an alert with the run's `correlation_id` and exits
  non-zero (or pages) — never a silent exit 0.
- A QC critical fail routes `qc_fail_alert` to the channel.
- A pre-close readiness check (is the gateway authed + two-sided fraction healthy at ~18:00) so the
  failure is caught before the close, not after.

## Links

[[deferred-disconnect-alert]], `platform-deploy-stack-ownership` (owns the systemd/alert stack).
Audit source: the 2026-06-15 capture-chain audit (P0-1, P1-3).
