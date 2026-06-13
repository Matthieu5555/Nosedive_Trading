# ibkr-unattended-reauth — close the ~daily SMS-2FA wall so the week of capture runs unattended

> **Source:** TARGET §5.9 (operational connectivity) + §2.2 (the unattended EOD week is an
> end-of-week deliverable) + the 2026-06-08 autonomy audit (unattended re-auth flagged) + ADR
> 0031 (hosted OAuth 1.0a path). **The end-of-week goal (§2.2) assumes "the EOD capture has run
> unattended and banked a gap-free week"** — today that assumption is not met: the session needs
> a human SMS login roughly daily, so a single missed re-login silently drops a capture day.

## The gap

The CP Gateway session has three clocks; the babysitter (`scripts/eod_babysitter.py`,
`CpRestSession`) self-heals only the first two:

1. **Idle timeout** — `tickle` (~60 s heartbeat) resets it. Handled.
2. **Brokerage-session drop** (`authenticated:true, connected:false`) — `session.reauthenticate()`
   (POST `/iserver/reauthenticate`) revives it **without** an SMS. Handled.
3. **SSO expiry (~daily)** — nothing in the cookie-session path can revive it; IBKR requires a
   fresh browser login, which on this account triggers an **SMS 2FA code on the account-holder's
   phone** for **both live and paper** logins. The babysitter logs a loud `ALARM` and stops —
   then a human must re-run `scripts/ibkr_gateway_login.py` and key the SMS code.

So the cookie-Gateway path **minimizes but cannot eliminate** the daily re-login. The only truly
unattended transport in the tree is the **hosted OAuth 1.0a** path (`IBKR_CP_*`, ADR 0031:
`cp_rest_lst.py` / `cp_rest_oauth.py` / `live_capture.live_basket_source`) — its Live Session
Token is acquired headless with no SMS. That code is built and gate-tested against fakes, but it
is **not in production** because Self-Service OAuth enrollment hit the "Enable OAuth Access → 400
not authenticated" wall (README `gateway_basket_source`, the attended cookie fallback that wall
forced). Until OAuth enrollment clears, the unattended week is not real.

## Scope (this leaf)

- **Drive OAuth 1.0a to a working unattended LST acquisition** end to end against the real hosted
  CP Web API: resolve the enrollment wall (consumer registration / access-token state), then prove
  `build_signed_cp_rest_transport` → `live_basket_source` acquires an LST and captures **with no
  human and no SMS**. The crypto (`cp_rest_lst.py`) and signer (`cp_rest_oauth.py`) already exist;
  this is the operational bring-up + whatever real-endpoint fixes the live exchange surfaces.
- **Make the SSO-expiry ALARM actionable.** The babysitter already *detects* the unrevivable state
  (clock 3) but only `print`s the ALARM. Route it through the alert-delivery seam
  (Telegram/email) so a required manual SMS re-login is a push, not a line in
  `/tmp/eod_babysitter.log` nobody reads. (Shared seam — alert delivery itself is sub-lane 4 of
  `execution-operational-hardening`; this task owns *raising* the IBKR-specific event, not the
  generic delivery transport.)
- **Document the operator runbook** for the residual manual path (when OAuth is unavailable):
  the headless SMS login, the dedicated second username (so the backfill never knocks out the
  live feed — one username = one brokerage session), and the pre-close verification.

## Out of scope / boundaries

- The generic alert-delivery transport (Telegram/email client) — that is
  `execution-operational-hardening` sub-lane 4. This task **emits** the IBKR re-auth-needed event
  into it; it does not build the channel.
- Order/booking auth (3A/3B) — read-only capture only, per R4 / ADR 0042.
- No new transport: REST/OAuth only (R4 — the CP-REST path is *the* IBKR path; the TWS socket
  path stays parked).
- Do **not** resurrect Saxo/Deribit (ADR 0042).

## Depends on / blocks

- Blocks the §2.2 "unattended week of capture" deliverable being **true** rather than assumed.
- Pairs with the alert-delivery sub-lane of `execution-operational-hardening` (the channel this
  routes into).
- Independent of the analytics/strategy lanes.

## Done criteria

OAuth 1.0a LST acquisition runs against the real hosted CP Web API and a close-capture banks a
real SX5E grid **with no human login and no SMS** for at least one full session; OR, if hosted
OAuth enrollment is still blocked, the SSO-expiry ALARM is **delivered** (push, not log-only) and
the manual SMS-relogin runbook + dedicated-username setup is documented and verified to recover a
session before the next close. The capture week's unattended-ness is then a measured fact, not an
assumption. Gate green (the broker-free CI seam unchanged; live bring-up is a smoke run, not pytest).
