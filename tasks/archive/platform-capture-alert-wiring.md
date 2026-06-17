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

---

## ✅ BOTH OPEN ITEMS CLOSED (2026-06-17, D2) — over C4's landed delivery seam

C4 (`d944b41 feat(infra): real alert-delivery channel`) landed the shared delivery seam +
concrete channel BEFORE D2; D2 then consumed it (no fork). Final wiring:

### C4 owns the seam + transport (landed) — `infra/orchestration/alert_delivery.py`
- `AlertSink` Protocol: `channel: str` property + `deliver(alert, context=None) -> DeliveryResult`.
- `JournaldAlertSink` (safe default), `WebhookAlertSink` (concrete channel),
  `resolve_alert_sink(env)` (env-driven selection), `severity_for(alert)`,
  `deliver_alerts(sink, alerts, context) -> list[DeliveryResult]`.
- `_qc` in `eod_stages.py` already routes `qc_fail_alert` + `coverage_breach_alerts` through it,
  with `default_stages_builder(alert_sink=...)` injectable.

### D2's slice (now landed on top, no duplication)
- **(a) Pre-close readiness check** — `algotrading.infra_ibkr.preclose_readiness`. Pure
  `evaluate_readiness(*, authenticated, two_sided_fraction, min_two_sided_fraction)
  -> ReadinessVerdict` (`.ready/.reasons/.detail/.exit_code`; reason codes `READY`,
  `NOT_AUTHENTICATED`, `TWO_SIDED_BELOW_FLOOR`, `NO_QUOTE_OBSERVATION`; `None` fraction ⇒ not
  ready). Thin `__main__`: `python -m algotrading.infra_ibkr.preclose_readiness` probes the real
  gateway via `CpRestSession.authenticated()` and the floor at
  `qc_threshold.quote_integrity.min_two_sided_fraction`, exits non-zero when not ready.
- **(b) Closed the silent-green gap** — new pure builder `degenerate_close_alert(...)` in
  `alerts.py` (kind `ALERT_DEGENERATE_CLOSE`) fires when no basket was captured OR baskets were
  captured but 0 combined-surface grid cells. `_qc` now (1) routes it through C4's
  `deliver_alerts` and (2) **forces the escalation to `ESCALATION_PAGE`** so `eod_runner` returns
  non-zero (engaging systemd `OnFailure=`) instead of exit 0. `degenerate_close` added to C4's
  `_CRITICAL_KINDS` so `severity_for` classifies it critical.

### ⚠️ ASSUMPTION for the capture/Stream-C manager to relay
`probe_two_sided_fraction` in `preclose_readiness.py` currently returns `None` (⇒ readiness
reports "no quote observation" → not ready). A real lightweight pre-close two-sided probe (a
minimal snapshot of the current chain reporting `two_sided_count / option_row_count`) is **not
yet wired** — the heavy snapshot machinery lives in `cp_rest_close_capture._snapshot_events`. The
pure decision logic already accepts a real fraction; only the `probe_two_sided_fraction` body
needs filling. Flagged rather than fabricated.
