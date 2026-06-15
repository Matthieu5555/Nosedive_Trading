# 0031 — IBKR historical market data over the Client Portal REST API, authenticated with OAuth 1.0a

- **Status:** accepted, 2026-06-06. Extends [[0024-ibkr-rest-transport-alongside-tws]].
- **Date:** 2026-06-06.
- **Implements:** roadmap **P0.3 / 1C** (underlying daily OHLC backfill) and **OQ-2** (IBKR is the
  historical source) — the roadmap now lives in `TARGET.md` (`documentation/roadmap-index-analytics.md`
  was removed with the `documentation/` tree). Honours the owner/prof mandate that the **Client
  Portal REST API** is the path.
- **Relates to:** [[0023-nautilus-runtime-spine-and-library-leverage]], [[0024-ibkr-rest-transport-alongside-tws]],
  [[0025-nautilus-host-catalog-topology]], [[0019-one-immutable-raw-model]], [[0011-blueprint-as-plan-of-record]].

## Context

We already run a working Client Portal (CP) REST transport: `cp_rest_adapter` (live snapshot +
WebSocket, read-only `/iserver/marketdata/*` per [[0024-ibkr-rest-transport-alongside-tws]] §4) and
`cp_rest_session` (`/tickle`, `/iserver/auth/status`). The roadmap needs **historical daily OHLC
bars** (index + every constituent) for the ticker charts and forward-built option history.

A web-sourced audit (deep-research, 2026-06-06) established: the CP Web API exposes
`/v1/api/iserver/marketdata/history` (current; `bar=1d`, `period` up to 15y; `hmds/history` is
deprecated). Its native session model requires a **daily post-midnight reauthentication** and a
**~6-minute tickle keepalive**, with *no IBKR-supported automation* of the interactive login — which is
why an earlier reading judged REST poor for unattended use. Follow-up research refuted that as the
whole story: **OAuth 1.0a** (a non-interactive Live Session Token, ~24h) removes the daily login and
2FA entirely; the `ibind` project demonstrates OAuth 1.0a working on individual accounts, and `IBeam`
(Dockerised CP Gateway + TOTP auto-login) is a proven fallback. So the REST path the owner mandated is
genuinely reliable unattended — the trick is OAuth, not the interactive Gateway.

## Decision

1. **Fetch historical daily OHLC via the CP REST endpoint** `/iserver/marketdata/history` (`bar=1d`),
   extending the existing CP REST transport. **No TWS / IB Gateway desktop, no Nautilus historical
   client.**
2. **Authenticate with OAuth 1.0a** (Live Session Token), **implemented in our own `cp_rest`
   transport/session** (referencing `ibind`'s implementation), signed with **pycryptodome** (not the
   abandoned pyCrypto). This makes the REST session unattended-capable — no daily interactive login or
   2FA.
3. **Keep the session alive** with a tickler; open/maintain the brokerage session (`ssodh/init`) and
   wait for `established:true` before requesting history.
4. **Use a dedicated second IBKR username** for the unattended backfill, so it never knocks out the
   live-snapshot feed (one username = one brokerage session across all IBKR platforms).
5. **Wrap in retry/backoff**, schedule away from IBKR maintenance windows, and honour the
   5-concurrent-request cap and the history "warmup" call.
6. **IBeam (Dockerised CP Gateway + TOTP)** is the documented fallback if OAuth 1.0a registration
   proves unworkable for the account.

## Consequences

- New dependency: **pycryptodome** for OAuth signing, plus a small OAuth 1.0a module in
  `packages/infra-ibkr`. No second REST client library is added (per owner decision, implement
  in-house).
- The read-only invariant of [[0024-ibkr-rest-transport-alongside-tws]] §4 extends to include the
  history GET endpoint — still strictly read-only.
- Keeps the prof's REST direction and reuses our transport; Parquet stays the immutable record
  ([[0019-one-immutable-raw-model]]).
- Residual risks (carried into the 1C spec): individual-account OAuth 1.0a is slightly off IBKR's
  official happy-path (support sometimes denies it, though it works in practice); IBKR maintenance
  windows cause unavoidable outages — hence retry/backoff and off-window scheduling.

## Alternatives considered (rejected)

- **TWS-API path (ib_async + IB Gateway + IBC)** — robust unattended (single weekly auth), but adds a
  desktop/gateway process and reverses the owner/prof REST mandate. Held only as a contingency.
- **Nautilus `HistoricInteractiveBrokersClient`** — capable of daily bars but only over TWS/IB Gateway,
  same objection.
- **ib_insync** — archived/unmaintained (maintainer deceased); excluded outright.
- **Adopting `ibind` as a second REST client** — duplicates our existing transport; owner chose to
  implement OAuth in-house and reference ibind instead.
- **CP Gateway interactive login (no OAuth)** — the daily post-midnight reauth has no supported
  automation and breaks unattended operation; IBeam mitigates but is pre-1.0 and flakier than OAuth.
