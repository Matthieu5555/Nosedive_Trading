# Runbook — the ~daily IBKR SSO re-auth (manual SMS path)

You were woken (06:00, a pushed alert) because the CP-gateway **SSO session expired** — clock 3 of
the [three session clocks](README.md#the-three-cp-gateway-session-clocks). The babysitter self-heals
the other two with no human; this one needs a fresh browser login that fires an **SMS 2FA code** on
the account-holder's phone. This page is the recovery, start to finish. It is the residual manual
path that exists **only until the unattended OAuth re-auth lands** — see
[the OAuth blocker](#the-oauth-enrollment-blocker-why-this-is-still-manual) at the bottom; that is
the thing the owner must clear to make this page obsolete.

> **Why a push at all?** The babysitter used to only `print` the ALARM into `/tmp/eod_babysitter.log`,
> which nobody reads. It now ALSO delivers an `sso_reauth_needed` alert (classified **critical**)
> through the shared C4 alert-delivery seam (`infra.orchestration.alert_delivery`) — webhook /
> Telegram / email if `ALGOTRADING_ALERT_WEBHOOK_URL` is set, journald otherwise. So the alert that
> woke you IS this event. The loud local log + journald line are still written as the
> delivery-of-last-resort.

## TL;DR — the recovery, three commands

```sh
# 1. Re-login (status + SMS login if needed + brokerage session). Idempotent.
uv run --with selenium python scripts/ibkr_login.py
#    If an SMS challenge fires, in a SECOND shell drop the texted 6-digit code:
printf '123456' > /tmp/sms_code.txt

# 2. Confirm the gateway is data-ready (the same seam production uses, not a curl).
uv run python scripts/eod_healthcheck.py

# 3. Pre-close readiness (run before the next close; ~18:00 CET for Eurex).
uv run python -m algotrading.infra_ibkr.preclose_readiness
```

All three exit 0 when green. If `eod_healthcheck.py` is still red after step 1, re-read its
remediation line — it prints the exact next command.

## Step 1 — the headless SMS login

The server has no GUI; the login is driven headless by Selenium.

    uv run --with selenium python scripts/ibkr_login.py

`ibkr_login.py` is the wrapper: it checks status first and **logs in only if needed**, then opens
the brokerage session (`ssodh/init`) and verifies. The low-level browser-only step it wraps is
`scripts/ibkr_gateway_login.py` — prefer the wrapper, which also does the brokerage-session step.

Credentials come from the repo-root `.env` (`IBKR_USERID` / `IBKR_PASSWORD`; see `.env.example`).

**The SMS is risk-based, not guaranteed.** A re-login on a recently-idle session often re-logs with
**no SMS at all** — do not assume the text is a blocker; watch the script's output. When a challenge
*does* fire, the script waits on a watched file; drop the texted code into it from a second shell:

    printf '<the 6-digit code>' > /tmp/sms_code.txt

## Step 2 — the dedicated SECOND username (do this once, it prevents the worst failure)

**One IBKR username = one brokerage session.** If a backfill / ad-hoc query logs in with the **same**
username the live feed uses, IBKR moves the brokerage session to the new login and **silently knocks
the live capture off the line** — the babysitter then sees a competing session (clock 3) and you get
woken for an outage you caused.

**Provision a second IBKR username** (free, via Account Management → Users & Access Rights, read-only
data permissions are enough). Then:

- **Username A** — reserved for the live close-capture feed. Only `ibkr_login.py` /
  `eod_babysitter` / the systemd timers ever use it. This is the `IBKR_USERID` in the deployment
  `.env`.
- **Username B** — for any backfill, exploratory pull, or second concurrent session. Run those with
  `IBKR_USERID` / `IBKR_PASSWORD` overridden to B (a separate `.env` or inline env), so B's session
  never touches A's line.

Rule of thumb: **never run a second login as username A while a close is pending.** If you must, do
it as B.

## Step 3 — pre-close verification (don't go back to bed on a half-fixed session)

Before the next close, prove the session will actually capture. Two checks, both exit 0 when green:

    uv run python scripts/eod_healthcheck.py                       # gateway auth + timer armed + last close banked
    uv run python -m algotrading.infra_ibkr.preclose_readiness     # ~18:00 readiness, the close-specific probe

`eod_healthcheck.py` (D1) probes three things through the real `CpRestSession` seam:
gateway authenticated, a capture timer armed with a future fire, and the last close banked (no
silent failure). `preclose_readiness` (D2) is the close-specific ~18:00 probe. Run both; if either
is red, its output names the remediation. Only then is the session recovered before the close.

## What "recovered" means

`eod_healthcheck.py` green **and** `preclose_readiness` green **and** the next close banks a real
grid (visible in the run-state ledger / the next morning's health check showing the date as
fully-healthy). Until OAuth clears, expect to do this roughly daily.

## The OAuth-enrollment blocker (why this is still manual)

**This entire manual page exists because the truly-unattended path is BLOCKED.** The only transport
in the tree that re-auths with **no SMS and no human** is the hosted **OAuth 1.0a** Live-Session-Token
path (ADR 0031: `cp_rest_lst.py` / `cp_rest_oauth.py` → `live_capture.live_basket_source`). The crypto
and signer are built and gate-tested against fakes, but the path is **not in production** because:

- **Self-Service OAuth enrollment hits a wall:** the IBKR portal step **"Enable OAuth Access" returns
  `400 not authenticated`**, so the consumer/access-token state never reaches a usable Live-Session
  enrollment. We cannot acquire a real LST against the hosted CP Web API until this clears.
- **What was tried:** the headless cookie-session fallback (`gateway_basket_source`, the attended
  login this page documents) — which works but cannot eliminate the ~daily SMS, because the cookie
  session's SSO clock expires daily.
- **Stuck state:** consumer registration / access-token enrollment is incomplete on the IBKR side
  behind the `400 not authenticated` wall; this is an **account/enrollment** action, not a code fix.
  Driving OAuth LST acquisition against the real hosted API is a documented dead-end until it clears
  — do not burn engineering time attempting it.

**Owner action required:** clear the IBKR Self-Service OAuth enrollment ("Enable OAuth Access →
`400 not authenticated`). Until then, the unattended-week goal (TARGET §2.2) is **NOT MET** — every
session day still depends on a human following this page, and a single missed SMS re-login silently
drops a capture day. Tracked by `tasks/ibkr-unattended-reauth.md`.
