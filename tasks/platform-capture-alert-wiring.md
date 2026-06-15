# platform-capture-alert-wiring — a failed/degenerate close must page, not exit 0 silently

**Owner:** Matthieu · **Lane:** `platform-` · **Priority:** P0 (before trusting the unattended week)

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
