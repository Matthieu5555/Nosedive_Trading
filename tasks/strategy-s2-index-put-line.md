# strategy-s2-index-put-line — S2 systematic short-put production line on the index (allocation factory)

> **Source:** TARGET §3 (S2 — Allocation Factory, course p.128–130) + §5.4 (stress screen) +
> §6 (kill switch). The deliberate opposite tail to S1; the first backtester case (§7.8).

## The gap
No strategy object exists (`packages/strategy` is an empty skeleton). No line-capacity / steering
/ kill logic anywhere. The premium (index downside IV > realized) is real and the index option
chain is captured today, but nothing runs the rolling short-put line.

## Scope — the S2 strategy object (rules, not infra)
- **Production line:** sell one ~3%-OTM (≈25Δ), ~30-day index put per day; the chain + delta-band
  selection already capture the candidate strikes.
- **Line capacity:** a config cap on open contracts (course: 30, rolling so one expires daily) —
  capacity is typed config (ADR 0028), not a literal.
- **Steering rule:** move the strike distance (2.5% / 3% / 4% below market) to control assignment
  frequency — a config-driven rule, not discretion.
- **The strategy contract (§1/§3):** premium = index downside IV > realized; intended Greeks =
  short downside vega, positive theta; kill = sharp sustained drawdown (short left tail) flattens
  the line — this strategy is *why* the book needs the stress screen (§5.4) and a kill switch (§6).
- Margin/assignment capacity is sized up front (the course's InvWC number) — depends on margin
  forecasting (§5.9), noted not built here.

## Depends on / blocks
- [[execution-fills-position-store]] (the rolling line is a booked, rolling position) + the index option
  chain capture (landed) + the delta-band / tenor-bracket selection (landed).
- The kill switch + margin forecasting in [[execution-operational-hardening]] (§5.9) — S2 is their first
  consumer; cross-link, do not build them here.
- **First backtester target:** [[strategy-backtester]] replays S2 through a banked stretch + 2008
  stress (course 2021-vs-2008, p.129–130).

## Done criteria
An S2 strategy object runs the rolling short-put line with a config line-capacity cap and a
config steering rule, exposes its named contract (premium/signal/Greeks/kill), and emits a daily
sell decision + a flatten-on-kill decision; the same object is callable in research/backtest/paper/
live (§6); capacity and steering are typed config; unit-tested on the course's rolling-line cycle;
gate green.
