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

## State (2026-06-16) — follow-ups LANDED
The three open follow-ups plus the BFF endpoint landed on the research machine, building on it
(reinventing none of it):
- **`StoreBackedBacktestData`** (`backtest/store_data.py`) — the production data path. Reads the
  as-of `projected_option_analytics` cell for each leg's grid coordinate, pins the concrete
  contract (right/strike/expiry) from that row, and rebuilds the `ContractValuationInput` from the
  same row (spot = `forward_price`, carry == 0; vol = `implied_vol`; multiplier/currency injected).
  Adds **no** compute, exactly like `StoreBackedDispersionData`/`StoreBackedGammaData`. Signal half
  reuses `signal_snapshot_from_store`. Look-ahead proven by a recording-store seam test (only the
  loop `as_of` is ever read, in order).
- **`TransactionCostModel`** (`backtest/costs.py`) — explicit `commission_per_contract` +
  `slippage_rate` × priced notional, charged at entry on the same `as_of`. `BacktestConfig.costs`
  defaults to `NO_COST` (gross, byte-identical). Surfaced as `DayResult.transaction_cost` /
  `cumulative_net_pnl` and `BacktestSummary.total_transaction_cost` / `total_net_pnl`.
- **`reconcile_shadow` / `ShadowReport`** (`backtest/shadow.py`) — the production-shadow machine.
  Drives the **same** `run_strategy` step + `daily_entry_fires` predicate (capacity off the booked
  line), concretizes the intended legs through the same `BacktestData` seam, and diffs
  net-by-contract signed qty vs injected `BookedFill`s — per-day constructed-vs-booked drift. The
  strategy layer can't import execution (it's above), so `BookedFill` is layer-neutral and the
  caller above execution (BFF / ops script) fills it from the execution fills ledger.
- **BFF `POST /api/backtest/run`** (`apps/frontend/.../routers/backtest.py`, mounted additively) —
  launches a store-backed S2 backtest and returns the full output (summary perf/net/cost/drawdown/
  Sharpe/turnover/stress, `cumulative_attribution`, per-day `days`). F-STRAT consumes it.

Four test layers: unit (`test_backtest_costs.py`, independently-derived cost numbers + the shadow
diff), seam/contract (`test_backtest_store_data.py` — adapter reads vs seeded store, oracle reprice),
integration (store-backed `run_backtest` end-to-end on a 2-day store; shadow match + drift;
`test_backtest_api.py` over the BFF TestClient), no-look-ahead recording-seam proofs in both the
store path and the shadow. `check-lookahead-bias` skill re-run — no violations.

**Deferred:** a P&L-level shadow (live realized vs backtest realized on the same booked line) — the
constructed-vs-booked drift check is the one that historically bit; the P&L-level one is the next
depth. The store adapter assumes `carry == 0` and one multiplier/currency per index (index-only
universe), not a per-contract instrument-master join.

Gate green (2026-06-16): ruff + mypy + lint-imports clean; `uv run pytest` = 2384 passed, 12 skipped.
