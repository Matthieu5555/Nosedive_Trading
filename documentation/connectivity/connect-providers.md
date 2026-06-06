# Connecting to data providers and launching a run

A practical, broker-by-broker guide to connecting a market-data provider and capturing an option
chain, backed by facts **verified live on 2026-06-03** (in the pre-merge tree).

> **Ported from the pre-merge reference tree (2026-06-05).** The broker connector/capture scripts
> this guide invokes (`deribit_collector_run.py`, `saxo_oauth.py`, `ibkr_bootstrap.py`, …) lived in
> the pre-merge `scripts/` directory and have **not yet been relocated** into the canonical monorepo
> tree — only the plot/export tooling has (`scripts/plot_live_surface.py` and the `export_*`/
> `reconstruct_sample` utilities). Until the connector scripts are ported, treat their command lines
> below as the *intended* shape of the workflow and the live broker facts (URLs, payload shapes,
> entitlement walls) as the load-bearing content. The per-broker verified facts now live under
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

- `uv sync` once (installs the workspace). IBKR also needs the optional group: `uv run --group ibkr ...`.
- Create a local `.env` at the repo root with the keys for the broker(s) you use (the per-broker
  variables are listed in the steps below). `.env` is gitignored; never commit credentials.
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
uv run --group ibkr python scripts/ibkr_bootstrap.py
# 2. Option capture — discover the chain and stream delayed ticks for an ATM contract:
uv run --group ibkr python scripts/ibkr_probe_option.py --symbol SPY --duration 15
```

Expected (step 2): `spot ~ ...` then ticks for bid/ask/last/close. Two gotchas the probe handles and
production code must too: **request options on `exchange=SMART`** (a specific exchange + arbitrary
strike returns *Error 200, no security definition*), and **pick the strike nearest spot** (the full
range is huge). *Error 10091* ("requires additional subscription") is the **real-time** wall, not a
bug — delayed still flows. Details in `packages/infra-ibkr/README.md`.

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
