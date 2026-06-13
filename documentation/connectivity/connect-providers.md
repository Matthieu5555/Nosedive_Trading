# Connecting to data providers and launching a run

A practical, broker-by-broker guide to connecting a market-data provider and capturing an option
chain, backed by facts **verified live on 2026-06-03** (in the pre-merge tree).

> **Ported from the pre-merge reference tree (2026-06-05).** The broker connector/capture scripts
> this guide invokes (`deribit_collector_run.py`, `saxo_oauth.py`, …) lived in the pre-merge
> `scripts/` directory and **most have not yet been relocated** into the canonical monorepo tree —
> the plot/export tooling has (`scripts/plot_live_surface.py` and the `export_*`/`reconstruct_sample`
> utilities), and **`scripts/ibkr_bootstrap.py` is now ported** (the IBKR smoke test below). Until the
> remaining connector scripts are ported, treat their command lines below as the *intended* shape of
> the workflow and the live broker facts (URLs, payload shapes, entitlement walls) as the load-bearing
> content. The per-broker verified facts now live under
> `packages/infra-{saxo,ibkr,deribit}/README.md`. Provider runs through the web app go via
> `algotrading.infra.orchestration.provider_flow` (`IbkrFlow`/`SaxoFlow` from the pre-merge tree are
> gone — collection is unified on the push `RawCollector` seam, ADR 0027).

## Scope — read this first (honest status)

| Path | Status |
|---|---|
| **Deribit (crypto)** — capture → SVI surface, A→Z | ✅ fully working, free, no auth (the reference path) |
| **Deribit run from the web app** | ✅ working |
| **Saxo (equity options)** — connect + capture delayed ticks (script) | ✅ verified live (ASML, 151 ticks) |
| **IBKR (equity options)** — connect + capture delayed ticks (script) | ✅ verified live (SPY, 48 ticks) |
| **Saxo / IBKR run from the web app** | ❌ not wired yet (only Deribit; equity `provider_flow` wiring is pending) |
| **Equity capture → SVI surface (full A→Z)** | ❌ not yet (Saxo "Stage B" wiring pending) |

So today: **Deribit is end-to-end (incl. the app); Saxo and IBKR are verified for *capture* via the
standalone scripts**, not yet through the app or all the way to a surface. The per-broker steps below
are the proven way to confirm a connection and that option data flows.

## Prerequisites (all brokers)

- `uv sync` once (installs the workspace). IBKR also needs the optional extra: `uv sync --extra ibkr`
  (the extra is `nautilus-trader[ib]`; it is **not** a `--group`). ⚠️ The lightweight
  `IbkrTransport` (and so `ibkr_bootstrap.py`) imports `ib_async`, which is **not yet declared** by
  the `ibkr` extra — until it is added, install it alongside (`uv pip install ib_async`). Tracked as
  a follow-up.
- Copy `.env.example` → `.env` at the repo root and fill in the keys for the broker(s) you use (the
  per-broker variables are listed in the steps below). `.env` is gitignored; never commit credentials.
- **Never live-trade.** These flows are read-only / market-data only. For Saxo the read-only guarantee
  is **broker-side** (register the app without the "Write" claim — see below).

---

## 1. Deribit (crypto) — free, no account, the reference path

No credentials, no subscription. BTC/ETH options are liquid 24/7 on the public API.

```bash
# Capture ~30s of the live BTC chain into the local raw store (data/, gitignored):
uv run python scripts/deribit_collector_run.py --seconds 30 --min-days 10 --max-days 45
# Reconstruct the captured day into an SVI surface:
uv run python scripts/deribit_reconstruct.py --currency BTC
```

Expected: a reference spot, forward maturities (quality=ok), solved IV points, and converged SVI
slices. **From the web app:** start the BFF + frontend and trigger a run with provider `DERIBIT`,
underlying `BTC` (this is the only provider the app runner supports today).

---

## 2. Saxo (equity options) — delayed data is free

Delayed 15-min option data is **free** on a funded live account; the ~7 EUR Euronext L1 subscription
is only for **real-time**. (Sim is Forex-only — options require the **live** environment.)

### One-time setup

1. On <https://www.developer.saxo> register a **Live** application. Use grant type **Code**, add a
   redirect URI (e.g. `http://localhost:8765/callback`). **Register it WITHOUT the "Write"/trade
   claim** — this is the broker-enforced guarantee the credential cannot place orders. (There is no
   per-app AssetType list; data access is account + session level.)
2. In **SaxoTraderGO → My Profile → Other → Open API Access → Enable** (accept the terms). This
   gates all non-Forex market data over the API — easy to miss.
3. Put `SAXO_CLIENT_ID`, `SAXO_CLIENT_SECRET`, `SAXO_REDIRECT_URI`, `SAXO_ENV=live` in `.env`.

### Run

```bash
# 1. Get fresh tokens (opens a browser; live tokens are short-lived, so re-run when they lapse):
uv run python scripts/saxo_oauth.py --env live
# 2. Baseline — does ANY market data flow? (FX is free real-time; stock is free delayed):
uv run python scripts/saxo_probe_price.py --env live --symbol EURUSD --asset-type FxSpot
uv run python scripts/saxo_probe_price.py --env live --symbol ASML   --asset-type Stock
# 3. Are OPTIONS entitled? (read-only single-option InfoPrices):
uv run python scripts/saxo_probe_option.py --env live --symbol ASML
# 4. Full capture — discover the chain and stream ticks:
uv run python scripts/saxo_collector_test.py --env live --symbol ASML
```

Expected (step 4): `Found N contracts [OK]` then ticks for bid/ask/delta/gamma/vega/theta/mark_iv/
open_interest/last. `NoDataAccess` means that exchange isn't entitled (a real-time wall), not a bug —
delayed should still work. Verified Saxo facts live in `packages/infra-saxo/README.md` (streaming URL,
payload shape, IV unit).

---

## 3. IBKR (equity options) — delayed data is free

Delayed data (`market_data_type=3`) is **free**; real-time needs a paid subscription. The TWS/Gateway
path has **no OAuth** — it connects to a locally running desktop app over a socket. (A separate
Client Portal REST transport also exists; see `packages/infra-ibkr/README.md` and ADR 0024/0025.)

### One-time setup

1. Start **TWS** or **IB Gateway**, log in (paper account is fine), and enable the API
   (Configuration → API → Settings → "Enable ActiveX and Socket Clients").
2. Note the host/port/client id (Gateway paper default is `127.0.0.1:4002`). Put `IBKR_HOST`,
   `IBKR_PORT`, `IBKR_CLIENT_ID` in `.env`.

### Run

```bash
# 1. Smoke test — connect, clock skew, resolve underlying, one stock snapshot (exit 0 = healthy):
uv run --extra ibkr python scripts/ibkr_bootstrap.py
# 2. Option capture — discover the chain and stream delayed ticks for an ATM contract:
uv run --extra ibkr python scripts/ibkr_probe_option.py --symbol SPY --duration 15
```

Expected (step 2): `spot ~ ...` then ticks for bid/ask/last/close. Two gotchas the probe handles and
production code must too: **request options on `exchange=SMART`** (a specific exchange + arbitrary
strike returns *Error 200, no security definition*), and **pick the strike nearest spot** (the full
range is huge). *Error 10091* ("requires additional subscription") is the **real-time** wall, not a
bug — delayed still flows. Details in `packages/infra-ibkr/README.md`.

### Client Portal REST OAuth 1.0a — the path the EOD capture actually uses

The TWS socket above is for interactive probing. The **daily EOD close-capture** (`scripts/eod_run.py`
→ `packages/infra-ibkr` `live_basket_source` / `collect_live_basket`, ADR 0024/0031) runs over the
**hosted Client Portal Web API with OAuth 1.0a**, not the socket — it needs no desktop app and runs
unattended via a ~24h Live Session Token. The runner keys live-vs-empty on the credentials: with every
required `IBKR_CP_*` var present it captures a real chain; with any missing it logs an empty basket and
exits 0 (the clean no-capture day). **This is why a bare `eod_run.py` captures nothing until the env is
provisioned.**

One-time setup: register a consumer key, access token/secret, and two RSA keys (signing + encryption)
in the IBKR Self-Service OAuth portal, then set in `.env` (see `.env.example` for the full list and the
PEM-path convention):

| Var | What |
|---|---|
| `IBKR_CP_CONSUMER_KEY` / `IBKR_CP_ACCESS_TOKEN` / `IBKR_CP_ACCESS_TOKEN_SECRET` | OAuth registration triple |
| `IBKR_CP_SIGNING_KEY_PEM` / `IBKR_CP_ENCRYPTION_KEY_PEM` | **file paths** to the registered RSA keys |
| `IBKR_CP_DH_PRIME` (+ optional `IBKR_CP_DH_GENERATOR` / `IBKR_CP_REALM` / `IBKR_CP_BASE_URL`) | the access-token → LST Diffie-Hellman exchange |

**Look-ahead note:** the live path is a *snapshot* of current quotes, valid only for the current
session day. A `--trade-date` in the past does **not** reconstruct a past option chain (CP REST has no
historical option-quote endpoint); past dates are served by the underlying-OHLC `/iserver/marketdata/
history` backfill (`CpRestHistoryCollector`), not the live basket source.

### Escape hatch — the local CP Gateway (no Self-Service OAuth enrolment)

If the Self-Service OAuth portal will not enrol the account — the common
**"Enable OAuth Access" → `HTTP 400 "You are not authenticated"`** wall on the agreement-submit
step (a browser-session bug on IBKR's side, not our code) — you do **not** need the `IBKR_CP_*`
artifacts at all. The **same** EOD capture runs over IBKR's locally-running **Client Portal
Gateway** with a browser-login **cookie** session (ADR 0024, `oauth_signer=None`), keyed on a single
opt-in flag:

```bash
# 1. Download + run IBKR's Client Portal Gateway (clientportal.gw — the Java bundle), then:
open https://localhost:5000        # log in (paper is fine) until it says "Client login succeeds"
# 2. Flip the flag and run the normal EOD capture — it now uses the Gateway, not OAuth:
IBKR_CP_GATEWAY=1 uv run python scripts/eod_run.py --calendar XNYS
```

`scripts/eod_run.py` picks the source in this order: **`IBKR_CP_GATEWAY` set → the local Gateway**
(cookie); else **`IBKR_CP_*` present → the hosted OAuth path**; else a clean no-capture day. The
trade-off is honest: the Gateway needs a **browser re-login ~daily** (the SSO cookie lapses), so this
is the **manual/attended** path — fine to prove capture and run by hand, whereas the OAuth block is
the one that runs **unattended** under the systemd timer. Same `RawMarketEvent` rows either way; the
look-ahead note above applies identically (it is still a current-session snapshot). Override the
Gateway URL with `IBKR_CP_GATEWAY_URL` only for a non-default listener.

`scripts/ohlc_backfill.py` honours the **same** `IBKR_CP_GATEWAY` selection, so the daily-OHLC
history backfill (the candlestick data — index underlyings + their constituents) runs over the
Gateway too. **Full step-by-step setup: [`ibkr-gateway-quickstart.md`](ibkr-gateway-quickstart.md).**
One caveat proven live: CP REST caps a history request at **~999 daily bars (~4 years)** and there is
no pagination yet, so one backfill run reaches back ~4 years per ticker, not the full history.

---

## Troubleshooting (verified meanings)

| Symptom | Meaning | Fix |
|---|---|---|
| Saxo `401` on `/ref/...` | access token expired | re-run `saxo_oauth.py --env live` |
| Saxo `NoDataAccess` on prices | that exchange's data not entitled | use a free delayed underlying, or subscribe (real-time) |
| Saxo WS `HTTP 404` | wrong streaming host/path or missing `contextId` | the code is fixed; ensure `--env live` |
| IBKR `Error 200, no security definition` | wrong exchange (not SMART) or strike not listed | request on SMART; pick a real ATM strike |
| IBKR `Error 10091` | real-time data needs a subscription | use delayed (`market_data_type=3`) — it is free |
| `'charmap' codec can't encode` | a non-ASCII char printed on a Windows cp1252 console | already fixed in the scripts; keep script output ASCII |

## Current limitations (do not assume these work yet)

1. **The web app runner supports only Deribit.** Saxo/IBKR runs from the frontend need the equity
   `provider_flow` façades (pending). Use the scripts above for Saxo/IBKR.
2. **Equity capture → SVI surface (full A→Z) is not yet wired.** Capture (ticks) is verified; the
   reconstruction-to-surface on real equity data needs Saxo "Stage B" (delayed-provenance `exchange_ts`,
   an underlying spot feed, `mark_iv` consumption).
3. **Live runs are manual** — there is no CI coverage of the network path; the proof is the scripts
   above, run by hand against a live session.
4. **Credentials/sessions are required and time-limited** — Saxo tokens lapse; IBKR needs TWS/Gateway
   running. "Launch a run" assumes that setup is in place.
5. **The broker connector/capture scripts are not yet in the canonical `scripts/`.** Only the plot/
   export tooling has been relocated so far (`plot_live_surface.py`, the `export_*` and
   `reconstruct_sample` utilities). The command lines above describe the intended workflow; the
   connector scripts themselves are a follow-up port.

_Last updated: 2026-06-03 (verified live: Deribit, Saxo ASML, IBKR SPY); ported & re-pointed 2026-06-05._
