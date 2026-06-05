# The big picture

Plain-language summary of the Volatility Infrastructure roadmap (the ThomasOssen
document, `ThomasOssen/1780037915_industrial_roadmap_volatility_infrastructure_v4.pdf`),
plus how we intend to build it with the least code.

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

## Status (2026-06-05)

This plan is the active direction, reaffirmed by
[ADR 0023](.agent/decisions/0023-nautilus-runtime-spine-and-library-leverage.md): **Nautilus is
the runtime spine**, and the platform leans on every well-built library it can. Two refinements to
the IBKR-centric sketch above: market data now comes from **three** brokers — **IBKR via
Nautilus's own adapter**, and **Saxo + Deribit via our own adapters** (Nautilus ships neither),
all three normalizing into the one catalog the engine replays. An interim decision to drop the
Nautilus dependency (ADRs 0007/0020) was reversed by 0023.
