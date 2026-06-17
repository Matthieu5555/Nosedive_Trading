# 0055 — Deploy via per-user systemd on the shared box; docker-compose dropped

- **Status:** accepted, 2026-06-17 (D1 deploy-stack ownership).
- **Date:** 2026-06-17.
- **Implements:** the deployment surface of the unattended week — `platform-deploy-stack-ownership`.
- **Relates to:** [[0032-unattended-scheduling-via-systemd-timers]] (the timer mechanism this
  deployment uses), [[0035-index-registry-and-per-index-capture-schedule]] (the per-index schedule
  the timers fire), [[0023-nautilus-runtime-spine-and-library-leverage]]. The operating contract
  lives in [`scripts/systemd/README.md`](../../scripts/systemd/README.md), not here.

## Context

The old `documentation/connectivity/server-deployment-plan.md` (now purged) anticipated a
`docker-compose.yml` plus a containerised headless IB-Gateway (`gnzsnz/ib-gateway-docker`) and a
supervised continuous collector. That plan predates the stack that actually shipped. As of the
2026-06-14 audit the deployment that runs the unattended week is **per-user systemd timers** on the
shared server (`eod-capture@*.timer` → `eod_run.py`, plus `data-backup.*`), and it is green. The
compose path was never built. "Deferred forever vs. not yet" was left ambiguous; this ADR settles it.

## Decision

**The chosen deployment is per-user systemd on the shared box. The `docker-compose.yml` + headless
IB-Gateway container is dropped, not deferred.**

Rationale:

- **systemd already runs the stack and is green.** ADR 0032 already chose systemd timers over an
  orchestration platform for the *scheduling*; a container around the same one-shot adds an image to
  build and a daemon to supervise for no capability the timers lack.
- **The close-capture is a per-close one-shot, not a long-running service.** A container's value is
  supervising a resident process; there is no resident process here — the timer fires `eod_run.py`,
  it exits, done. The babysitter (the one long-running piece, for timer-less boxes) is a plain
  detached process, not something a compose service buys us.
- **The gateway is driven headless without a container.** `scripts/ibkr_login.py` /
  `ibkr_gateway_login.py` log the CP Gateway in via headless Firefox + SMS 2FA on the bare box. The
  `gnzsnz/ib-gateway-docker` image solves the *same* "no GUI on the server" problem we already solve;
  it would be a second, redundant mechanism.
- **One fewer moving part for the unattended week.** Adopting (not rebuilding) the green stack is the
  whole charter of the owning task; introducing compose now is a refactor that risks the week for no
  gain.

## Consequences

- The deploy source of truth is `scripts/systemd/README.md` (install, cadence, the three gateway
  session clocks, exit codes, alarm triage) — the live replacement for the dead deploy-plan doc.
- The single readiness probe is `scripts/eod_healthcheck.py` (gateway authenticated + timer armed +
  last capture banked).
- No `docker-compose.yml` is committed; references to it as a future artifact are repointed to this
  ADR / the README.

## Reopen if

A concrete need appears that systemd-on-one-box cannot meet: multi-host capture, a reproducible
clean-room gateway image for CI, or a hosted runtime that mandates containers. Then build the
compose stack and amend this ADR — don't reintroduce it silently.
