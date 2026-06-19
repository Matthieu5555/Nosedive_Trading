# The big picture

Plain-language summary of the Volatility Infrastructure roadmap (the ThomasHossen
document, `ThomasHossen/industrial_vol_roadmap.pdf`),
plus how we intend to build it with the least code.

Where this is all going — the end-state capability map and the current end-of-week
goal — lives in [`TARGET.md`](TARGET.md). This file is the *how-we-build* companion to
that *what-done-looks-like*.

## What we're building

The document is a build manual, not a strategy. It tells us how to build the
plumbing for a volatility (options) trading operation, and deliberately says
nothing about how you'd actually make money with it. We're building the kitchen,
not writing the recipes — the point is that the kitchen is so clean and
well-organized that any recipe can be cooked in it later.

What it builds is a pipeline that turns raw Interactive Brokers market data into
trustworthy options analytics, in five layers. First, connectivity: a reliable
IBKR connection that reconnects itself and never silently dies. Second, raw
capture: write down every tick exactly as it arrived and never edit it — this raw
record is sacred. Third, normalized market state: clean up the mess by picking
sensible prices, discarding junk quotes, and lining everything up by time.
Fourth, derived analytics, which is the real math — reconstruct the forward
price, back out implied volatility from option prices, fit a volatility surface,
price options, and compute the Greeks. Fifth, portfolio and risk: combine
positions with the analytics and ask what you lose if the market drops 5% and
volatility spikes.

The sixteen steps in the document are just this pipeline broken into buildable
chunks, each with a pass/fail test — from step 1, prove you can connect to IBKR
without placing any orders, up to step 16, hand it off so someone else can run it.

## The four ideas that make it valuable

These are the non-negotiable rules, and they're the whole reason to build it this
way. Determinism: the same inputs always produce identical outputs, no
randomness, no drift. An immutable raw layer: you can always recompute everything
from the original ticks, so nothing is lost or quietly changed. Provenance on
everything: every number knows where it came from — which ticks, which code
version, which config. And the same code for live and replay: re-running
yesterday uses the identical code path as today's live run, so there are no
"worked in backtest, broke in live" surprises.

Why this is the smart foundation — the part beyond the document — is that those
four rules hand you the upper floors almost for free. The replay engine is your
backtest data substrate. The clean analytics store is your machine-learning
feature store. The provenance and versioning are your guarantee of no look-ahead
cheating in research. And the strategy being deliberately left out is the clean
seam where backtesting, research, and ML plug in later, as read-only consumers
that never tangle themselves into the plumbing.

In one sentence: build a boring, bulletproof, strategy-agnostic data-and-pricing
backbone first, and done right it makes everything glamorous — backtests, alpha
research, ML, live execution — cheap and safe to add on top later.

## How we build it with the least code

Two libraries do the heavy lifting for this backbone. Nautilus (nautilus_trader)
gives us IBKR connectivity, contract and option-chain discovery, a Parquet data
catalog, and a replay/backtest engine — that's steps 1, 2, 3, the raw side of 4,
and 13 out of the box. Its "same engine for backtest and live" property is
exactly the document's same-code-path replay mandate, so step 13 comes nearly for
free instead of being a restatement harness we write ourselves. QuantLib (plus
py_vollib for fast implied-volatility inversion) does pricing, Greeks, the
American pricer, day-counts, and calendars.

Everything else is small glue, not a framework: duckdb over Parquet for the
curated analytics layer, numpy/scipy/polars for the bespoke math,
pandas-market-calendars for sessions, and APScheduler + structlog +
prometheus-client for orchestration and observability.

The backtest frameworks beyond Nautilus (vectorbt, qlib, backtrader, lean,
hftbacktest, kungfu, zipline, barter-rs) do not reduce code for this backbone.
They are strategy/backtest engines — none of them build forwards, invert implied
volatility, fit a vol surface, or run scenario risk, which is the actual work
here. They re-enter at the upper floors (research, ML), not at this layer.

What stays our own code is the genuinely bespoke core, and it splits in two.
First, the analytics math, written as plain pure functions independent of any
framework: spot mid/last fallback, the parity forward, quote quality control, IV
diagnostics, the SVI surface fit with no-arbitrage checks, and the scenario grid.
Second, one thin Nautilus actor that feeds market state into those functions and
writes their outputs — implied-vol points, surface parameters, risk — to our own
Parquet/DuckDB layer with the provenance stamps Nautilus won't add for us. Because
that actor runs identically in Nautilus's live and backtest engines, our surfaces
and risk get recomputed the same way live and in replay, with no extra code.

The one seam to hold firm: keep the math as pure functions with Nautilus only as
the data-transport and replay shell calling them. That keeps the QuantLib/SVI core
testable on its own, lets us feed those same functions from a plain loop if
Nautilus ever gets in the way, and keeps the strategy-agnostic boundary intact —
analytics never reach up into strategy code.

## Status (2026-06-13)

This plan is the active direction: **Nautilus is
the runtime spine**, and the platform leans on every well-built library it can. An interim decision
to drop the Nautilus dependency was reversed.

**Scope:** the
platform is **index-options-only**, **IBKR is the sole live broker**, and **EuroStoxx-50 (SX5E) is
the sole live index** (SPX parked). Single names are index *constituents*, never standalone
underlyings. The earlier multi-broker sketch (Saxo + Deribit adapters) was retired with that pivot
— do not resurrect it. Market data is IBKR via Nautilus's own adapter, normalizing into the one
catalog the engine replays.

## Library leverage — forward view (2026-06-07)

A library-by-library audit (the 2026-06-07 `AUDIT-library-leverage` review and its REP0–REP8
backlog, now retired to git history) confirmed the principle above is real in the
tree, and mapped where each proven library should *grow* as the roadmap advances. The guiding
rule holds: lean on libraries for plumbing; keep the deterministic analytics math our own.

- **Nautilus** is the spine but still under-used. The big future adoptions: a live `TradingNode`
  (today only its backtest engine drives the system); its typed `Bar`/`BarType` model + the IBKR
  adapter's historical-bar path for the daily-OHLC capture (roadmap 1C/1E); and `OrderFactory`/
  `Order`/`Strategy`/`ExecutionEngine` as the candidate **3B** sign-and-send path (3A landed as a
  pure, paper preview ticket — the inert object 3B signs). The open design question is whether
  Nautilus's `ParquetDataCatalog` *becomes* the raw store — deferred until the provenance/
  immutability invariants are proven to survive it.
- **QuantLib** is already at its right footprint (the American lattice). It earns more only when a
  **real term structure** arrives — non-flat discount/dividend curves and proper day-counters for
  the gated futures/carry work (1D). Not before; flat-rate one-liners do not need a `YieldTermStructure`.
- **py-vollib** stays the IV oracle. `py_vollib_vectorized` becomes worth adopting as the batch
  IV/Greek kernel only if full-chain throughput (a full index chain × multi-year history, 1F) is
  measured as a real bottleneck — keeping the scalar diagnostic solver as the per-contract path.
- **scipy** is the numerics workhorse going forward: `scipy.interpolate` (PCHIP/RBF) for the 1F
  surface regrid, and `scipy.stats`/`scipy.linalg` if parametric VaR or book decorrelation (2D)
  is ever built on top of today's full-reprice scenario engine.
- **duckdb / polars / pyarrow** — duckdb's `ASOF JOIN` is the pattern for *every* point-in-time
  alignment to come (1A constituents, 1C/1E option-vs-OHLC, 1H coverage QC). polars is mandated but
  currently idle; its first homes are the snapshot as-of (REP2) and the 1F projection + BFF
  serialization. `pyarrow.dataset` is the partition-management upgrade once the store reaches
  years × hundreds of constituents.
- **exchange-calendars** grows naturally as indices/venues are added (one registry entry each); it
  will own session/settlement/roll math for 1D futures via `sessions_window`/`next_open`. Today it
  resolves the Eurex (SX5E) session and close that drive the EOD capture timer.
- **pydantic** is the highest-leverage *future* play, twice over: as the typed BFF response contract
  (so the unit-carrying `{raw,dollar,unit}` shape and OpenAPI come free as Phase 2 multiplies
  endpoints) and as the config validation layer (retiring the hand-rolled reflective coercer). Both
  must respect the byte-stable-for-SHA-256 determinism constraint.
- **plotly** stays the single charting dependency — correct precisely *because* of the 3D surface
  requirement (the 2B PnL stress surface is another `go.Surface`). The web shell should pick up
  TanStack **Query** (never installed despite the ADR) and lean harder on TanStack **Table** as the
  dense Tab-2 grids arrive; the shadcn-vs-plain-CSS drift needs a ruling.
- **pycryptodome** has one genuine future job: the IBKR Live-Session-Token exchange (RSA/DH), which
  is still unwritten — the only place hand-rolling crypto is forbidden and the library is mandatory.
