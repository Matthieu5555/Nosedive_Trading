# platform-deploy-stack-ownership — give the live deployment stack a governing spec

The unattended-week story runs on a real deployment stack that was **built without a spec
tracking it**, while the only deploy task on the board described a different, now-superseded
slice (TWS-socket smoke; see
[archive/platform-server-deploy-plumbing](archive/platform-server-deploy-plumbing.md)). This
task adopts the stack that actually exists, makes its operating contract explicit, and carries
the deploy pieces still genuinely deferred — so "how the box runs the unattended week" has one
home instead of living only in script docstrings.

- **Owns:** the deployment/operations surface, none of it product compute — `scripts/systemd/`
  (`eod-capture@.service`/`.timer` per index, `eod-capture-alert.service`),
  `scripts/eod_babysitter.py` (keepalive + per-index close fire), `scripts/ibkr_gateway_login.py`
  (headless CP-REST login + SMS 2FA), `scripts/eod_run.py` (the fired capture entrypoint), the
  repo-root `.env.example`, and a `scripts/systemd/README.md` runbook (the `documentation/` tree is
  dead — do not put runbooks there; see [platform-doc-coherence-fix](platform-doc-coherence-fix.md)).
- **Depends on:** nothing new — the stack is landed and green. It depends *operationally* on the
  CP-REST session seam (`session_factory.build_gateway_session`) and the per-index exchange
  calendars that derive each `session_close`.
- **Relates to:** [ibkr-unattended-reauth](ibkr-unattended-reauth.md) (the ~daily SSO-expiry wall
  the babysitter can only *alarm* on, not fix — that task closes it via OAuth); the alert path
  here is the delivery half of [execution-operational-hardening](execution-operational-hardening.md)'s
  alert-delivery sub-lane — cross-ref, don't duplicate.
- **State going in (audited 2026-06-14):** the systemd units, babysitter, alert service, and
  CP-REST headless login all exist and landed (`890974f`, `f4e8aa7`, `73e5338`). What is **not**
  built: the `docker-compose.yml` + `gnzsnz/ib-gateway-docker` service, a supervised continuous
  collector entrypoint, and a written deploy runbook reconciled with reality. (The old deploy-plan
  doc under the dead `documentation/` tree is stale residue, not a source — it is purged, not
  truthed-up, by [platform-doc-coherence-fix](platform-doc-coherence-fix.md). The runbook this task
  writes is its replacement.)

## What to do (ordered)

1. **Document the contract of what exists.** In `scripts/systemd/README.md` (next to the units —
   **not** in the dead `documentation/` tree), write the operating contract for the landed stack: which unit fires when,
   the three CP-gateway session clocks the babysitter self-heals vs. alarms on, exit-code meaning,
   and the operator action for each alarm. This is the missing runbook, derived from the code, not
   re-invented.
2. **Replace the dead deploy-plan doc, don't truth it up.** The old
   `documentation/connectivity/server-deployment-plan.md` is residue of an abandoned tree — it is
   purged by [platform-doc-coherence-fix](platform-doc-coherence-fix.md), not maintained. The
   `scripts/systemd/README.md` from step 1 is its live replacement; make sure nothing else still
   links the dead doc as the deploy source.
3. **Decide the docker-compose question explicitly.** Either build the deferred
   `docker-compose.yml` + headless IB-Gateway service, or record in an ADR/this spec that systemd
   on the shared box is the chosen deployment and compose is dropped — so "deferred forever vs.
   not yet" stops being ambiguous. Do not leave it as a silent gap.
4. **One smoke path for the deployment itself.** A documented, runnable check that the deployed
   box is healthy — gateway authenticated, next timer armed, last capture banked — so a Monday
   pre-close verification (TARGET §2.2) is a command, not a memory.

## Test surface

Mostly docs + ops, so the checks are operational:
- The runbook's stated unit cadence matches `systemctl list-timers` on the box.
- `scripts/systemd/README.md` exists and names only artifacts that exist; the dead
  `documentation/connectivity/` deploy-plan doc is gone (purged by the doc-coherence task), not
  re-pointed.
- The docker-compose decision is recorded (a file exists, or an ADR/spec line says it is dropped).
- Any new ops helper is ruff/mypy clean (root gate stays green); no `packages/**` touched.

## Done criteria

The landed deployment stack has a written operating contract; the deploy-plan doc tells the truth;
the compose question is decided on the record; a single documented command verifies a deployed box
is ready for a close. No product compute touched.

## Gotchas

- **Adopt, don't rebuild.** The stack works and is green — this task makes it *legible and owned*,
  not rewritten. A refactor here risks the unattended week for no capability gain.
- **Don't absorb the re-auth fix.** The ~daily SSO wall is [ibkr-unattended-reauth](ibkr-unattended-reauth.md)'s
  to close; here the babysitter only needs to alarm correctly, which it does.
- **The alert *delivery* channel** (where the ALARM goes — email/SMS/push) belongs with
  [execution-operational-hardening](execution-operational-hardening.md)'s alert sub-lane; wire to it,
  don't fork a second delivery mechanism.
