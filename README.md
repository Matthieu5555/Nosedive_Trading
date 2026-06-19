# Nosedive — Index Volatility Trading Platform

Nosedive turns raw Interactive Brokers market data into trustworthy options
analytics and a trading cockpit for **index options** — today on the EuroStoxx 50
(SX5E). It captures the option chain at the close, reconstructs an honest
volatility surface, measures where the market is rich or cheap, lets you compose
and stress a position, and books it on paper — then tells you whether the P&L
came from the risk you meant to hold.

The platform is built around one economic idea: **options markets quote
expectations, and the money is in the measured gap between what is implied and
what actually realizes** — implied vol vs realized vol, index vol vs the vol of
its basket, front-month vs back-month, put-side vs call-side. Each gap is a
premium someone pays for protection, and each can be harvested *if you can measure
it precisely and verify you were paid by the gap you targeted, not by luck.* The
differentiator is not order entry — it is **measurement and attribution**.

---

## What it does

The platform is a pipeline, from raw ticks to attributed P&L:

1. **Capture** — a self-healing IBKR connection records the index option chain
   (and its constituents' prices) at the close, exactly as it arrived. The raw
   record is immutable: every later number can be recomputed from it.
2. **Market state** — junk quotes are discarded, sensible prices are picked, and
   everything is lined up by time into a clean, point-in-time snapshot.
3. **Analytics** — the real math: reconstruct the forward, back out implied
   volatility, fit the volatility surface, price the options, and compute the
   Greeks. A quality gate flags any surface the data can't honestly support.
4. **Signals** — the gaps the strategy book trades: implied vs realized vol, term
   slope, put–call skew, index-vs-basket dispersion, IV rank.
5. **Composition, risk & booking** — compose a multi-leg position, see its
   combined Greeks, shock it against named or custom scenarios, and book it on
   paper behind a write barrier.
6. **Attribution & reconciliation** — decompose realized P&L by Greek (a
   delta/gamma/vega/theta waterfall), and reconcile the book against the broker
   account.

Two rules make every number trustworthy: **determinism** (same inputs → identical
outputs) and **provenance** (every value knows which ticks, which code version,
and which config produced it). Re-running yesterday uses the *same* code path as
today's live run, so there are no "worked in backtest, broke in live" surprises.

## The cockpit

The web app is a four-tab operator cockpit:

- **Données** — the reading page. Scorecards (ATM vol, skew, convexity, realized−
  implied), the index price chart with its constituents, the 3D volatility surface,
  one tenor selector driving the put/call smile, the per-strike price structure and
  the Greeks, and the dispersion strip.
- **Risque** — compose → see → shock → explain: a position composer, the booked
  book, on-demand and historical stress scenarios, and the by-Greek attribution
  waterfall.
- **Ordres** — the order ticket (paper booking behind a password barrier; live
  transmission stays disarmed), broker reconciliation, and the backtester.
- **Operations** — the operator dashboard: system health, run control, and data
  freshness.

## Quick start

You need **Python 3.12+** with [`uv`](https://docs.astral.sh/uv/), **Node.js 20+**,
and (for live capture) Interactive Brokers credentials. Analytics and the cockpit
run on the bundled sample data without any broker login.

```bash
# 1. Install the Python workspace (creates .venv at the repo root)
uv sync

# 2. Install the web app's dependencies
cd apps/frontend/web
npm install

# 3. Launch the whole stack — API backend + web UI, one command
./start.sh
```

Then open **http://127.0.0.1:5173**. `./start.sh status` shows what's running,
`./start.sh restart` brings it back up fresh, and `./start.sh stop` shuts it down.

To capture a fresh end-of-day close from Interactive Brokers (instead of the
bundled sample), log in and run the close from the repo root:

```bash
just login          # headless IBKR gateway login (SMS 2FA)
just eod            # capture every enabled index for today
```

## How it's built

- **Backend** — Python, on top of [Nautilus Trader](https://nautilustrader.io/)
  for the runtime spine and Interactive Brokers as the sole live broker. Captured
  data lands in columnar Parquet/DuckDB tables; the pricing, surface and risk
  engines are pure functions over those tables.
- **API** — a FastAPI backend-for-frontend that reads the analytics tables and
  serializes them to JSON; it never writes (only the end-of-day capture does).
- **Web** — a React + Vite single-page app. Charts use Plotly (3D surface, smile,
  heatmaps) and TradingView Lightweight Charts (candlesticks).

## Scope

Index options only, EuroStoxx-50-first; Interactive Brokers is the sole live
broker. Adding or parking an index is configuration, not code.
