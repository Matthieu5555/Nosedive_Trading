# server-deploy plumbing — non-compute connectivity prep (slice A)

A deliberately **small, non-compute** slice of the larger
[`documentation/connectivity/server-deployment-plan.md`](../documentation/connectivity/server-deployment-plan.md)
(still a DRAFT). It exists so the team can confirm a headless IB Gateway is actually answering
**before** the gated capture pipeline (roadmap 1C/1G) is built — without touching any business
parameter or compute path.

- **Owns:** a repo-root `.env.example`, `scripts/ibkr_bootstrap.py`, and the connectivity docs under
  `documentation/connectivity/`. **Touches no `packages/**` code** and no config under
  `core/config/**` — so it does **not** collide with C7 (config hardening, in flight) and does
  **not** breach the owner gate *"no new compute until C7 + reproducibility lock"*: this slice adds
  zero compute, only a connection smoke test and a credentials template.
- **Depends on:** nothing. The IBKR connectivity surface it drives already exists
  (`infra_ibkr.connectivity.ibkr_transport.IbkrTransport`, `connectivity.client_id_for`,
  `connectivity.SystemClock`).
- **Explicitly out of scope (deferred — "on finira après"):** the `docker-compose.yml` +
  `gnzsnz/ib-gateway-docker` service, the supervised continuous collector entrypoint, and the
  daily **cron / pre-close snapshot** (that is roadmap **1C + 1G**, gated behind C7 → Phase 0 →
  1A/1B). The **intraday-streaming v2 collector** stays a **nice-to-have**, not now.

## What to build

1. **`.env.example`** at the repo root — a committed template (the only `.env*` git-tracked, per
   `.gitignore` `!.env.example`). Carries: the IBC auto-login vars the `gnzsnz/ib-gateway-docker`
   image expects (paper username/password **placeholders**, `TRADING_MODE=paper`, Read-Only flag)
   and the IBKR socket vars (`IBKR_HOST`, `IBKR_PORT`, `IBKR_CLIENT_ID`, `IBKR_ACCOUNT`). **No real
   secrets, ever** — the public-safety hook rejects them, and rightly so.

2. **`scripts/ibkr_bootstrap.py`** — the smoke test promised by
   `documentation/connectivity/connect-providers.md` (pre-merge script, never relocated). It
   connects to a running TWS / IB Gateway, proves a real round-trip (broker clock + skew),
   resolves an underlying on `SMART`, and pulls one delayed stock snapshot. Exit codes:
   **0 healthy / 1 hard failure (no connect or no round-trip) / 2 soft (connected but no quote —
   e.g. an entitlement wall, Error 10091)**. Self-contained `.env` loading (no new dependency),
   ASCII-only output (the cp1252-console gotcha in the troubleshooting table).

3. **Doc re-point** — flip the "not yet relocated / does not exist yet" notes in
   `connect-providers.md` and `server-deployment-plan.md` for these two artifacts now that they
   exist; leave every gated/compute note (1C/1G cron, docker compose) untouched.

## Acceptance

- `.env.example` exists, is git-trackable, contains only placeholders.
- `uv run --extra ibkr python scripts/ibkr_bootstrap.py --help` works with no live gateway.
- Against a live paper Gateway: a clean session exits 0 and prints clock-skew + one snapshot;
  no gateway exits 1 with a clear message; an entitlement wall exits 2.
- ruff/mypy clean on the new script (root gate stays green); no `packages/**` or config touched.

## Gotchas

- **Cannot be verified end-to-end this weekend** — equity markets are shut until Monday and no
  Gateway is deployed yet. The `--help` path and lint are verifiable now; the live path is a
  Monday check. (The capture mechanism itself is the gated pipeline, not this slice.)
- Keep this slice strictly non-compute so the C7 gate and the in-flight C7 work are both respected.
