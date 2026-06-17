# Server deployment plan — SUPERSEDED

> **Status: SUPERSEDED (2026-06-17).** This was a pre-merge DRAFT plan for a compose-based,
> continuous-collector deployment that was never built. The stack that actually ships is per-user
> **systemd** on the shared box, and the compose path is **dropped on the record**. Do not treat
> this file as the deploy source.

The live deployment contract — install, what fires when, the three CP-gateway session clocks, exit
codes, and the operator action for each alarm — is:

- **[`scripts/systemd/README.md`](../../scripts/systemd/README.md)** — the operating contract.
- **[`.agent/decisions/0055-deploy-via-systemd-compose-dropped.md`](../../.agent/decisions/0055-deploy-via-systemd-compose-dropped.md)** — why systemd, why compose is dropped.
- **`scripts/eod_healthcheck.py`** — the single "is this box ready for a close?" probe.

The `collector_run.py` continuous-collector entrypoint this draft anticipated was not built either;
capture is a per-close one-shot fired by the systemd timers (ADR 0032 / 0035), not a resident
collector. Historical context for the original draft lives in git history.
