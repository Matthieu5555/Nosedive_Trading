# Server deployment plan — continuous paper-mode data collection

> **Status: DRAFT / plan-only.** No deployment code is written yet. Three decisions are still
> open (see § Open decisions). This doc freezes the agreed approach and the to-do list — notably
> the **server-admin tasks** — so the actual work is mechanical once the project is further along
> and the decisions are made.

> **Ported from the pre-merge reference tree (2026-06-05).** Config paths and the collector
> entrypoint have been re-pointed to the current monorepo layout. The repo-root **`.env.example`
> now exists** (added with `scripts/ibkr_bootstrap.py`, the non-compute connectivity slice). One
> referenced artifact still does **not yet exist** and is flagged inline where it appears: the
> standalone `scripts/collector_run.py` capture entrypoint (the gated 1C/1G work). The pre-merge flat
> `packages/infra/configs/{broker,collectors}.yaml` were superseded — IBKR capture config now lives
> in `packages/infra-ibkr/configs/capture.yaml`, and host/port/clock-skew are arguments to the
> Nautilus IBKR client builder (`infra_ibkr.connectivity.nautilus_ibkr.build_data_client_config`),
> not a separate `broker.yaml`.

## 1. Goal & scope

Run the project on a shared remote server so the **market-data collector runs unattended** against
a **paper** IB Gateway. This is infrastructure prep: continuous capture builds the data base that
later roadmap steps depend on.

**In scope (deployable today):**
- IB Gateway in **paper mode** (account id starts with `DU…`), headless on the server.
- The collector (a `RawCollector`-driven capture entrypoint — pre-merge `scripts/collector_run.py`,
  not yet relocated into the canonical `scripts/`) capturing quotes into the immutable raw store,
  supervised so it stays up.

**Explicitly out of scope:**
- **No live trading, no orders.** The collector never places orders by design, and the API is run
  Read-Only. The account holds **no funds**.
- **No "trading engine" yet.** The `execution` package (live orchestration, orders, PnL) is still a
  skeleton — there is nothing live to run full-time. "Run the engine full-time" means, for now,
  **run the data collection full-time**.

## 2. Target architecture

Everything is defined in the repo via a `docker-compose.yml` (to be written later). The IB Gateway
**binary is never committed** — it is proprietary IBKR software. Instead the compose file pulls a
community image that packages Gateway + auto-login + virtual display.

```
Linux server (Tailscale-only reachable)
├── service: ib-gateway   →  image gnzsnz/ib-gateway-docker
│     • bundles IB Gateway + IBC (auto-login) + Xvfb (virtual display)
│     • handles IBKR's mandatory daily restart automatically
│     • paper login + Read-Only API; socket on internal port 4002
│
└── service: collector    →  this repo, `uv sync --extra ibkr`
      • connects to ib-gateway over the internal network (localhost-equivalent)
      • runs the capture entrypoint under a supervisor (restart on exit)
      • writes the raw store to a mounted volume under the deployer's home

Secrets (paper login for IBC auto-login) → .env file, gitignored, NEVER committed.
End state: `docker compose up -d` brings up Gateway (self-logs-in) + collector together.
```

### Why IB Gateway (and where REST fits)

IB Gateway **is** the server/headless edition of TWS, and it speaks the TWS API protocol that the
codebase's Nautilus IBKR adapter targets (socket port `4002` for paper). The earlier "the code does
**not** speak the Client Portal Web API" caveat is **no longer absolute**: a custom Client Portal
REST/WS transport now exists alongside the TWS path (ADR 0024/0025, see
`packages/infra-ibkr/README.md`), selectable via a `transport` switch. For an unattended headless
paper collector, **IB Gateway over the TWS socket remains the simplest path** — it is the standard
self-hosting story and needs no separate browser-auth gateway. We deploy IB Gateway here.

### Why headless needs the Docker image

IB Gateway is a Java GUI app that (a) requires a logged-in window and (b) force-restarts once a day.
On a screen-less server it cannot survive alone. The `gnzsnz/ib-gateway-docker` image solves this:
**IBC** auto-logs-in and relaunches after the daily restart, **Xvfb** provides a virtual display.
This is the standard way to run Gateway unattended.

## 3. Security model

**Hard truth first:** whoever owns the server has **root** and can read everything on it. No
permission scheme hides data from the machine owner. In paper mode with no funds there is nothing
to steal, but it must be stated: isolation protects you from the *other non-admin users*, not from
the admin.

**The real "paper-only" guarantee is not permissions — it is that no live credentials ever touch
the server.** If only a paper login is present, even root cannot trade live: the live credentials
are simply not there.

Layers, strongest first:

| # | Layer | Protects against | Cost |
|---|---|---|---|
| 1 | **No live credentials on the box** (paper login only) | Anyone, incl. admin, trading live | free (discipline) |
| 2 | **Dedicated paper IBKR username** (separate login under the account) | The on-server login reaching anything but paper | ~5 min on IBKR |
| 3 | **Read-Only API** ticked in Gateway | Any order placement via the API, even paper | one checkbox |
| 4 | Deployer's files (repo, `.env`, data store) under `~user` mode `700`, **not** in shared `/srv/project` | Other non-admin users reading creds/data | one command |
| 5 | Dedicated Linux service user for Gateway + collector | Process isolation | nice-to-have |

Layers **1 + 3 + 4** are sufficient for this case; **2** makes it airtight; **5** is optional.

## 4. Open decisions (block the final compose/runbook, not this doc)

1. **OS** — assumed Linux (the `/srv/project` + per-user SSH setup implies it). Confirm.
2. **Which paper account** — a paper username owned by the deployer, **or** the server owner creates
   his own paper account and we use that (cleaner: the deployer's own IBKR login is never exposed).
3. **Run window** — collect only during **US market hours** (`America/New_York` 09:30–16:00) vs
   literally 24/7. Market-hours-only is the sensible default (no quotes overnight). The session
   window is enforced in the connectivity layer rather than a flat `broker.yaml`.

## 5. To-do list

### Server admin (server owner)
- [ ] Confirm OS is Linux; share distro/version.
- [ ] Install **Docker** + **Docker Compose** on the server.
- [ ] Confirm the deployer gets a **personal Linux user** (not a shared one) with a home dir.
- [ ] (Recommended) Replace the shared SSH password with **per-user SSH keys** — kills the
      weakest link (one password shared across everyone).
- [ ] Decide whether the owner provides his own **paper** IBKR account (open decision #2).
- [ ] Confirm outbound network access to IBKR endpoints is allowed from the server.

### Deployer
- [ ] Join the Tailscale network via the **personal invite link** (provided out-of-band) and verify
      the Tailscale app is connected.
- [ ] SSH to the assigned `user@host` (credentials provided out-of-band — **never** in this repo).
- [ ] Create / obtain a **paper** IBKR login (account id `DU…`).
- [ ] In Gateway settings: **Read-Only API ticked**, socket port `4002`, trusted IP for the
      internal Docker network.
- [ ] Keep the cloned repo, `.env`, and the data volume under `~user` (mode `700`), out of
      `/srv/project`.

### Repo work (later, when decisions are made — its own branch `chore/server-deploy`)
- [ ] `docker-compose.yml`: `ib-gateway` (image `gnzsnz/ib-gateway-docker`, paper) + `collector`
      (this repo).
- [x] Add a repo-root **`.env.example`** enriched with the IBC auto-login variables the image
      expects (paper username + password placeholders, `TRADING_MODE=paper`, Read-Only flag) plus
      the IBKR socket vars (`IBKR_HOST`, `IBKR_PORT`, `IBKR_CLIENT_ID`, `IBKR_ACCOUNT`). **Done.**
- [ ] Relocate a **collector entrypoint** into the canonical `scripts/` (pre-merge
      `scripts/collector_run.py` is not yet ported) and add a small **supervisor wrapper** so it
      runs continuously (loop with a long window + restart, or a market-hours scheduler honoring the
      session window), surfacing its `0/1/2` exit codes.
- [ ] Add a "server / Docker deployment" section to the connectivity docs (this directory) once the
      compose lands.

## 6. Connection runbook (reference)

The server is reachable **only over Tailscale**. Each member uses their **own personal invite
link**; SSH uses `user@host` with a password — **all of these are shared out-of-band and must never
be committed to this repo** (the public-safety hook will reject secrets, and rightly so). High-level
steps: join Tailscale → install/verify the Tailscale app → SSH (terminal or VS Code Remote-SSH) →
work in the deployer's home dir, not the shared project folder.

## 7. Config references (current locations)

- `packages/infra-ibkr/configs/capture.yaml` — IBKR strike selection, `n_expiries`, the discovery
  maturity window (the pre-merge `collectors.yaml` instrument list is now provider-scoped here).
- `infra_ibkr.connectivity.nautilus_ibkr.build_data_client_config(host=, port=, …)` — the socket
  host/port (defaults: IB Gateway `4002` paper / `4001` live) and market-data type; this replaced
  the flat `broker.yaml` host/port/reconnect block.
- **`.env`** (gitignored) for per-machine `IBKR_HOST` / `IBKR_PORT` / `IBKR_CLIENT_ID` /
  `IBKR_ACCOUNT` overrides — copy the committed **`.env.example`** template (now present) to `.env`.
- The collection entrypoint (pre-merge `scripts/collector_run.py`) — **not yet relocated** into the
  canonical `scripts/`; supervision for continuous runs is a to-do.

_Ported & re-pointed 2026-06-05 from the pre-merge reference tree._
