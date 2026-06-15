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

## State (2026-06-15)
**Research machine LANDED** (`packages/strategy/src/algotrading/strategy/backtest/`). The
production-shadow machine is the deliberate second build and is **not** done. What landed:
- `run_backtest(strategy, data, *, dates, config)` — day-by-day replay driving the **landed**
  substrate, reinventing none of it: the §6 harness (`run_strategy`, `context=BACKTEST`, the same
  call paper/live make) for the decisions, `position_risk` for the book lines,
  `attribute_realized_book` for the day-over-day per-Greek attribution (the §5.7
  "attribution-through-time" primitive), `worst_case` over a scenario grid for the stress column.
- Serious output in `BacktestResult`/`DayResult`/`BacktestSummary`: performance, max drawdown,
  Sharpe, turnover, exposure Greeks, stress losses, and `cumulative_attribution()` (named per-Greek
  P&L summed across the stretch — *which Greek paid*, not a Sharpe number).
- **No look-ahead by construction** (loop-variable `as_of` is the only date source; attribution
  start = strictly yesterday, end = strictly today). The `check-lookahead-bias` skill was run; a
  recording-seam audit test proves it mechanically.
- First §7.8 target met: **S2** (index short-put line) over a banked stretch + an adverse
  (spot-down + vol-up) regime — the course's 2021-vs-2008 method (p.129-130) industrialised. The
  engine drives S2's `decide_sell` (signal ∧ capacity) and the rolling daily roll-off.
- v1 ships the `BacktestData` protocol + an in-memory reference adapter (tested against
  hand-derived numbers and the landed pricer as an independent oracle, no canonical `data/`).

**Open follow-ups (not blocking):** the **store-backed `BacktestData`** (wire the landed
ADR-0043 grid-cell concretizer + the infra valuation join over a `trade_date`-narrowed grid read —
adds no compute, mirrors the S1/S3 store adapters); the **production-shadow** machine
(reconcile the same `run_strategy` step against booked paper/live fills); an explicit
**transaction-cost / slippage** model (v1 P&L is a gross upper bound on net).

Gate green: ruff + mypy + lint-imports clean; `uv run pytest -q` = 2225 passed, 12 skipped.
