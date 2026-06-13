# T-backtester — research backtester + production shadow (replay full point-in-time surface state)

> **Source:** TARGET §5.7 + §7.8. *"Substrate genuinely ready; the backtester itself does not
> exist. Natural next big build."* Not optional — a strategy is not real until backtest, paper,
> and live share the same logic.

## The gap
`grep backtest` hits only Nautilus/snapshot READMEs — no research backtester. The substrate IS
ready: immutable raw, byte-identical replay, as-of discipline, the same actor live/replay.

## Scope — two machines, one strategy object (the "one logic, four contexts" standard, §6):
1. **Research backtester** — "does this idea have edge?". Replays full point-in-time **market
   state** (surfaces, not just prices), realistic fills, expiry/assignment, margin, costs.
2. **Production shadow** — "would my live system have traded and produced the P&L I expect?"
   (catches implementation drift between research/paper/live).
- Serious output: performance, drawdowns, turnover, exposure, Greeks, stress losses, **and
  attribution through time** ("returns came from short vega + positive carry", not "Sharpe 1.4").
- **First concrete target:** replay **S2 (index put line)** through a banked stretch + an adverse
  regime — the course's own 2021-vs-2008 method (p.129–130), industrialized.

## Depends on
The strategy objects (2D + the strategy book), banked history depth, [[infra-second-order-greeks]]
for through-time attribution. Post-week per §5.7.

## Done criteria
Research backtester replays S2 over banked history with attribution-through-time; production
shadow reconciles to live/paper on the same logic; deterministic + replayable; gate green.
