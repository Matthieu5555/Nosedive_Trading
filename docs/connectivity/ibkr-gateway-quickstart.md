# IBKR Client Portal Gateway — quickstart (the no-OAuth capture path)

**TL;DR.** When the IBKR Self-Service OAuth portal won't enrol the account (the
`Enable OAuth Access → HTTP 400 "You are not authenticated"` wall), you do **not** need the
`IBKR_CP_*` OAuth artifacts. Run IBKR's local **Client Portal Gateway**, log in once in a browser,
and set **one flag** — the same EOD capture and the same daily-OHLC backfill then run over it:

```bash
# one-time: download + start the gateway (leave it running)
cd ~/ibkr/clientportal.gw && ./bin/run.sh root/conf.yaml      # listens on https://localhost:5000
#   ...then open https://localhost:5000 in a browser and log in (see step 3)

# then, capture / backfill over the gateway — note the IBKR_CP_GATEWAY=1 flag:
IBKR_CP_GATEWAY=1 uv run python scripts/eod_run.py     --calendar XNYS          # today's option close
IBKR_CP_GATEWAY=1 uv run python scripts/ohlc_backfill.py --index SPX --period 5y  # daily OHLC history
```

This is the **attended** path: the gateway's login cookie lapses roughly daily, so you re-login in
a browser each session. The unattended path (a systemd timer, no human) is the hosted **OAuth 1.0a**
one (`IBKR_CP_*` in `.env`) — that is the eventual production path, blocked today only by the OAuth
portal. Both authenticate the *same* Client Portal REST API and produce *byte-identical*
`RawMarketEvent` / `DailyBar` rows.

---

## Why two gateways exist (don't confuse them)

IBKR ships **two** different "gateways"; only one serves the REST API our code uses:

| Product | Protocol / port | Our code path | Auth |
|---|---|---|---|
| **Client Portal Gateway** (`clientportal.gw`) | **REST/WS on `:5000`** | `collect_live` + `CpRestHistoryCollector` (ADR 0024/0031) | browser-login **cookie**, or hosted OAuth 1.0a |
| IB Gateway (`gnzsnz/ib-gateway-docker`) | TWS socket on `:4002` | the Nautilus/TWS fallback (ADR 0025) | IBC auto-login |

This guide is the **Client Portal Gateway** (`:5000`) — the path that matches
`infra_ibkr.session_factory.build_gateway_session` and the `IBKR_CP_GATEWAY` flag. The TWS-socket
`gnzsnz/ib-gateway-docker` image is the *other* one and is not part of the deployment — compose was
dropped (see [`.agent/decisions/0055-deploy-via-systemd-compose-dropped.md`](../../.agent/decisions/0055-deploy-via-systemd-compose-dropped.md)).
How the box runs the unattended week lives in [`scripts/systemd/README.md`](../../scripts/systemd/README.md).

## 1. Prerequisites

- **Java 11+** on `PATH` (`java -version`). The gateway is a Java/vert.x app.
- A funded IBKR account (a **paper** account is fine for everything here — read-only market data).

## 2. Download + start the gateway

```bash
mkdir -p ~/ibkr && cd ~/ibkr
curl -O https://download2.interactivebrokers.com/portal/clientportal.gw.zip
unzip -o clientportal.gw.zip -d clientportal.gw
cd clientportal.gw && ./bin/run.sh root/conf.yaml
```

Leave it running. It prints `Open https://localhost:5000 to login` and listens on `:5000`
(self-signed TLS). To run it detached so it survives the shell:

```bash
cd ~/ibkr/clientportal.gw && setsid bash -c './bin/run.sh root/conf.yaml > ~/ibkr/gateway.log 2>&1' &
tail -f ~/ibkr/gateway.log     # watch startup
pkill -f GatewayStart          # stop it
```

## 3. Log in (browser)

Open **`https://localhost:5000`** — note the **`https://`** (the port is TLS-only; plain `http://`
gives `ERR_EMPTY_RESPONSE`). Accept the self-signed-cert warning (*Advanced → proceed to localhost*),
then log in (paper is fine) until you see **"Client login succeeds"**.

Headless server? Tunnel the port and open the URL on your laptop:

```bash
ssh -L 5000:localhost:5000 <user>@<server>     # then browse https://localhost:5000 locally
```

## 4. Verify the session

```bash
curl -sk https://localhost:5000/v1/api/iserver/auth/status
# expect: {"authenticated":true,"established":true,"connected":true,"competing":false,...}
```

`build_gateway_session` opens the brokerage session (`ssodh/init`) and waits for `established:true`;
if the gateway is down or not logged in it raises `SessionNotEstablishedError` (a loud failure, never
a silent no-capture).

## 5. Use it

The selection in both entrypoints is **`IBKR_CP_GATEWAY` set → local Gateway**; else **`IBKR_CP_*`
present → hosted OAuth**; else a clean no-op (exit 0).

```bash
# EOD option-chain close capture (only captures when the calendar's market is in/after session):
IBKR_CP_GATEWAY=1 uv run python scripts/eod_run.py --calendar XNYS

# Daily-OHLC history backfill for the candlestick charts (index underlyings + their constituents):
IBKR_CP_GATEWAY=1 uv run python scripts/ohlc_backfill.py                       # all enabled indices + constituents
IBKR_CP_GATEWAY=1 uv run python scripts/ohlc_backfill.py --index SPX --period 5y --no-constituents
```

The backfill writes the `daily_bar` table the frontend candlestick router
(`/api/price-history?underlying=SPX`) reads back. Verified live: `--index SPX --period 5y` persists
999 SPX bars (2022-06 → 2026-06), serialized straight into the front's candle shape.

## What the daily-history backfill can and cannot do

- ✅ **Index underlyings + their point-in-time constituents.** `history_requests_for` resolves each
  enabled index's conid and, unless `--no-constituents`, its as-of basket (1A membership) with each
  constituent's equity conid. `--as-of YYYY-MM-DD` picks the basket as it stood on that date.
- ⚠️ **CP REST caps a single request at ~999 daily bars (~4 years).** Asking `--period 15y` still
  returns only the most recent ~999 bars. **There is no multi-request pagination yet**, so one run
  backfills at most ~4 years per ticker. Deeper history (walking `startTime` backward across requests)
  is a tracked follow-up. (`bar`/`default_period` live in `infra-ibkr/configs/ibkr_history.yaml`.)
- ⚠️ **Some constituents may not resolve or may lack entitlement.** Non-US symbols especially can
  fail `secdef/search` (e.g. the index `SX5E` itself returns *"No symbol found"* — its IBKR symbol
  differs) or return no history without a data subscription. Those tickers are skipped, not fatal.
- ℹ️ The backfill is **resumable**: a ticker already on disk for `(provider, underlying)` is skipped,
  so a re-run only fills the missing tail (and so will **not** extend an existing ~4y window — that
  needs the pagination follow-up).

## Limitations (be honest about these)

1. **Attended, not unattended.** The gateway cookie lapses ~daily → browser re-login. Don't put this
   on the EOD systemd timer; that's the OAuth path's job (once the portal enrolment works).
2. **Live capture is a current-session snapshot.** A past `--trade-date` does not reconstruct a past
   option chain (no-look-ahead guard); past *underlying* prices come from this OHLC backfill.
3. **One brokerage session per IBKR username.** Running the backfill on the same username as a live
   feed will knock one out — use a dedicated second username for unattended backfills.

See `connect-providers.md` for the broker-by-broker context and the OAuth (`IBKR_CP_*`) path.
