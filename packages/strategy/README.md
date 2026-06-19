# strategy

The shared **Strategy spine** — the trunk every S1–S5 strategy and the backtester build on
(TARGET §1/§3/§6). Alpha layer: imports `infra`/`core` only, and `infra` is blind to it.

## TL;DR

A strategy here is a *contract* (TARGET §1): it names the premium it harvests, the signal that
triggers it, the Greeks it intends to hold, and its kill condition. This package turns that into
code: a typed, frozen `StrategyContract` (the four §3 columns as inspectable data), the
`Strategy` protocol every strategy object implements (entry / exit-kill decisions, a
construction step that emits a *stamped* 2A `Basket`, and an optional band-rebalance hook), and
the one-logic-four-contexts harness so research, backtest, paper, and live call the *same*
object identically. On that spine sits the first concrete strategy — **S1 dispersion**
(`s1_dispersion.py`, see its section below); S2–S5's rules are still owned by their S-tasks.

## What it does

The four pieces, each in its own module:

- **`contract.py` — `StrategyContract`.** A frozen record of the four §3 columns:
  `premium_harvested` (the named premium), `signal` (a `SignalKind`: the entry trigger),
  `intended_greeks` (an `IntendedGreeks` of signed `GreekSign` directions — the profile
  attribution checks realized P&L against), and `kill_condition` (the declared death mode). One
  instance per strategy; data the system can read, display, and check P&L against.
- **`signals.py` — `SignalSnapshot`.** The as-of-stamped input the entry decision *reads*. The
  strategy reads ρ̄ / IV-rank / RV−IV / term-slope; it does **not** compute them (that is the
  infra signal layer's job, a separate lane). This is the agreed shape today; when infra
  publishes its signal contract, `SignalSnapshot` becomes a thin alias of it and consuming code
  does not change. A missing reading is a labelled absence (`None`), never a fabricated zero.
- **`strategy.py` — the `Strategy` protocol.** A structural `Protocol` (implement by shape, no
  base class): `contract`, `decide_entry(as_of, signals)`, `decide_exit(market)`,
  `construct(as_of, basket_id)`, and an optional `rebalance(market)`. Every method is a **pure
  function of its injected arguments** — no clock, no live read, no store. That purity is what
  makes "research == backtest == paper == live" provable.
- **`delta_hedge_band.py` — `decide_delta_hedge`.** The shared band rebalance rule (course req
  #9, "Delta-hedge en bande") the `rebalance` hook delegates to, so S1/S3/S4 share **one** band
  decision rather than three copies. Given a current net delta and a typed `DeltaHedgeBand`
  (`target`, the economic-config `half_width` tolerance, and a `hedge_ratio` instrument
  convention), it **holds** while net delta stays within `half_width` of `target` and re-hedges
  **only on band exit**, sizing the hedge to return delta to target. A pure function — the
  course's "don't pin delta continuously, it bleeds spread" rule as one inspectable call.
- **`harness.py` — `run_strategy`.** The §6 calling convention: one function any of the four
  `StrategyContext`s invokes the same way on the same instance, returning a `StrategyStep`
  (the three decisions + the stamped `Basket` when entry fires). It adds no logic, so equal
  inputs give equal steps across contexts. It refuses a basket whose stamp does not match the
  strategy's own identity (`UnstampedBasketError`).

### The strategy-identity stamp (two infra seams)

The construction step emits a 2A `Basket` carrying `strategy_id == contract.strategy_id`. That
stamp is an **additive-nullable** field on the `infra.contracts.Basket` (default `None`; the
field, its validation, and its round-trip live in `infra`) so the same emitted set flows, named,
into both:

- **2D composition** (`infra/risk/book.py`) — layered as a named book layer (the `strategy_id`
  keys `BookLayerInput.label`).
- **per-strategy attribution** (TARGET §7.2) — P&L grouped by strategy and checked against
  `intended_greeks`.

`infra` reads the stamp as an opaque label; it never reads strategy *logic*.

## Quickstart

```python
from datetime import date
from algotrading.strategy import (
    run_strategy, StrategyContext, MarketState, SignalKind, signal_snapshot,
)

step = run_strategy(
    my_strategy,                       # any object implementing the Strategy protocol
    context=StrategyContext.BACKTEST,
    as_of=date(2026, 1, 5),
    signals=signal_snapshot(date(2026, 1, 5), {SignalKind.IMPLIED_CORRELATION: 0.62}),
    market=MarketState(as_of=date(2026, 1, 5), position_lines=()),
    basket_id="my-basket",
)
# step.entry / step.exit_ / step.rebalance are the decisions; step.basket is the stamped
# 2A Basket (present only when entry fired).
```

A complete, hand-checkable implementor lives in `tests/reference_strategy.py` (`ToyStrategy`) —
the fixture the seam and harness tests run against. It is **not** S1–S5; it exists only to prove
the spine.

## S1 — the dispersion strategy (the first strategy on the spine)

`s1_dispersion.py` is the flagship strategy object (TARGET §3 S1) — the first thing to
implement the `Strategy` protocol for real, and the first consumer of the per-side
vol surfaces. It harvests the **correlation premium**: when index ATM IV is rich relative to
the constituent ATM IVs on the same tenor (high implied correlation ρ̄), a book that is long
single-name vol and short the index monetises the gap as the names decorrelate.

- **`DispersionStrategy`** — pure over an injected `DispersionConfig` (the economic parameters:
  `index`, `top_n`, `straddle_tenor`, `entry_threshold`, …, sourced from typed platform config,
  never `.py` literals) and a `DispersionMarketData` (the as-of I/O seam). Entry fires when ρ̄ ≥
  the threshold; `construct` builds a long ATM straddle on each **point-in-time top-N
  constituent** (resolved through `top_n_by_weight` — never a hand-set list), routing the
  **call leg to the call wing and the put leg to the put wing**, plus a **synthetic
  short-forward index leg-pair** (short ATM call + long ATM put, `combined` wing) sized to
  flatten the straddles' net dollar delta. A negligible hedge is omitted; an unpriceable leg is
  refused (`DispersionConstructionError`), never silently dropped. Exit fires the §3 kill when
  net dollar-vega collapses (the long-vol thesis gone); `rebalance` re-flattens net delta by
  delegating to the shared `decide_delta_hedge` band rule (S1 is delta-flat, so its band targets 0).
- **`StoreBackedDispersionData`** (`dispersion_data.py`) — the store-backed implementor of
  `DispersionMarketData` for paper/live: it composes the as-of membership resolver and the pure
  `basket_risk` over a `trade_date`-narrowed grid read; it adds no risk math. Build a ready-to-run
  object with `dispersion_strategy(store, config, provider="ibkr")`.

- **`signal_snapshot_from_store`** (`signal_data.py`) — the as-of bridge from the persisted infra
  signal layer to the `SignalSnapshot` the strategy reads. It reads one day's `strategy_signals`
  partition for an index and surfaces the reference-tenor readings (ρ̄ on the index, plus per-name
  IV-rank / RV−IV / term-slope), preserving each reading's `subject`. This is what took S1's ρ̄
  entry from fixture-fed to live; it lives here, not in infra, because it touches both layers.

**v1 boundary:** v1 shorts the *forward* (delta only) and stays net long vol; v2 (short the index
*straddle* → a pure correlation spread) is the explicit upgrade, out of scope. S1 reads ρ̄ from
the `SignalSnapshot`, now sourced from the persisted infra signal layer via
`signal_snapshot_from_store`; the *realized*-correlation kill reading is still future, so
`decide_exit` uses the net-vega-collapse proxy for the position-side kill until then.

## S2 — the index short-put line (course p.128–130, "Allocation Factory")

`s2_put_line.py` is a different shape from S1/S3: not one delta-neutral structure but a **rolling
line** (TARGET §3 S2) — the deliberate **opposite tail to S1**. It harvests the **index left-tail
variance premium**: index downside implied vol runs richer than realized, so a systematic line
that sells one ~25Δ, ~30-day index put per day collects that premium as theta.

- **`PutLineStrategy`** — pure over a `PutLineConfig` (the economic parameters: `index`,
  `put_tenor`, the steered `put_delta_band`, `line_capacity`, `max_rv_minus_iv`,
  `exit_delta_ceiling`, from typed platform config, never `.py` literals) plus injected
  signals/market. It needs **no store-backed data adapter** — construction is config-only, the
  signal arrives through the existing `signal_snapshot_from_store` bridge, and the open-contract
  count for the capacity gate is derived by the caller from the booked line.
  - **Daily sell** — `decide_sell(as_of, signals, open_contracts=…)` is the operational decision:
    `ENTER` only when the premium is on offer (`decide_entry`: index `RV − IV ≤ max_rv_minus_iv`,
    i.e. implied richer than realized) **and** the line is under capacity (`line_at_capacity`).
    `construct` then emits the one short put to add, at the steered `put_delta_band` / `put_tenor`,
    routed to the **put wing**. `decide_entry` (the protocol method the §6 harness
    calls) is the signal half alone, so it stays pure of position state.
  - **Capacity** — `line_at_capacity(open_contracts)` is the pure cap rule (course: 30 open,
    rolling so one expires daily); at the cap the line stops adding even with the signal open.
  - **Steering** — the `put_delta_band` *is* the steering lever: moving it deeper OTM (a lower-Δ
    band) lowers assignment frequency. Config-driven, deterministic — a rule, not discretion.
  - **Kill** — `decide_exit` flattens when net delta breaches `exit_delta_ceiling` (the
    position-side proxy: short puts going ITM as spot falls — the short left tail hitting). With
    no ceiling configured it **holds and defers** the flatten to the execution kill switch.
  - **Rebalance** — a no-op: S2 carries its short-put delta intentionally (unlike S1/S3 it is not
    delta-neutral by rule), so there is nothing to band-hedge.

**Cross-lane seam:** the *enforcing* kill switch and the up-front margin/assignment sizing (the
course's InvWC number) live in `execution-operational-hardening` (§5.9/§6) — S2 is their first
consumer; this object emits the decision, execution enforces it. The signal layer must publish
index-level `IV_VS_REALIZED` for the live feed (like S1's ρ̄ before its source landed); research/
backtest inject the snapshot directly. **First backtester target** (§7.8): S2 replays through a
banked stretch + the 2008 stress (course 2021-vs-2008, p.129–130).

## S3 — the gamma-trading strategy (course p.107–108)

`s3_gamma.py` is the second concrete strategy object (TARGET §3 S3) — and the second consumer
of the shared `decide_delta_hedge` band rule (S1 hedges a synthetic forward; S3 hedges with
stock). It harvests the **gamma premium** on *one* cheap name: when a single name's implied vol
is low and realized vol comes in higher, a delta-neutral **long-gamma** structure scalps the
difference — each delta-band round trip banks the rectangle realized vol carves out, paid for
with theta.

- **`GammaStrategy`** — pure over an injected `GammaConfig` (the economic parameters: `index`,
  `option_tenor`, `entry_iv_rank_max`, `contracts`, `delta_band`, …, from typed platform config,
  never `.py` literals) and a `GammaMarketData` (the as-of I/O seam). Entry fires when the
  **cheapest** name's **IV rank** is at or below the threshold — the course ranking's "low IV
  expected to rise" (the opposite sense to S1's "ρ̄ rich → high triggers"); `construct` builds a
  **long ATM call** on that single name (routed to the call wing) plus a **short stock
  leg** sized to flatten the call's net dollar delta (Δ=0). A negligible hedge is omitted; an
  unresolvable cheap name / call delta / spot is refused (`GammaConstructionError`), never a
  naked directional call. Exit fires the §3 kill when **net dollar-gamma collapses** (the
  long-gamma thesis gone — "quiet drift + IV crush, gain < theta"); `rebalance` runs the p.108
  scalp cycle by delegating to `decide_delta_hedge` — hold inside the band, sell stock as delta
  rises past it, buy back lower, each round trip banking the rectangle.
- **`StoreBackedGammaData`** (`gamma_data.py`) — the store-backed implementor of
  `GammaMarketData` for paper/live: it picks the cheapest name from the banked `strategy_signals`
  IV-rank partition, prices the call's delta through the pure `basket_risk` over a
  `trade_date`-narrowed grid read, and reads the name's spot from the grid's `forward_price`
  (the pipeline pins `carry == 0` ⇒ forward == spot, so no second table is needed). It adds no
  risk math. Build a ready-to-run object with
  `gamma_strategy(store, config, reference_tenor="3m", provider="ibkr")`.

**v1 boundary:** v1 builds the **long call + short stock** form; the course's symmetric
alternative (long put + long stock — the same long-gamma/Δ=0 structure with the wings swapped)
is a documented, deferred mirror that changes no rule. The kill is the net-gamma-collapse
position-side proxy until the realized-vs-implied kill reading lands. S3 and S1 share a failure
mode (low realized vol) **on purpose** — the §3 overlap is held so the 2D book/correlation view
must surface it; it is not "fixed" here.

## S5 — the calendar-carry strategy (course p.42–45, the optional fifth)

`s5_calendar_carry.py` is the §3 **optional** fifth strategy (TARGET §3 S5). It harvests
**term-structure carry**: in contango the front month decays faster than the back at the **same
strike**, so a **short-front / long-back** calendar banks the theta differential while the long
back leg carries the vega. It is the same shape as S2 — pure over a `CalendarCarryConfig`, no
external market-data seam, because the structure is a fixed two-leg same-strike spread rather
than a sized hedge.

- **`CalendarCarryStrategy`** — pure over an injected `CalendarCarryConfig` (`index`,
  `front_tenor`, `back_tenor`, `strike_band`, `entry_slope_threshold`, `contracts`,
  `surface_side`, optional `exit_theta_floor`; the config rejects equal front/back tenors).
  Entry reads the **term-structure slope** the signal layer publishes (the front already renders
  the term-structure panel) and fires when the slope is at or above the threshold — contango,
  front decaying faster than back. `construct` emits the two-leg basket: a **short** front-tenor
  option and a **long** back-tenor option at the *same* `strike_band` and `surface_side`. The
  declared §3 contract is positive theta, long back vega, short gamma, ~flat delta; the kill is
  **front-month event repricing** that inverts the term structure (the front bid above the back,
  the carry reversing). Exit expresses that kill on the position side: a configurable
  `exit_theta_floor` flattens when **net theta** falls to/through the floor (the front decay no
  longer outpaces the back); with no floor it defers to the execution kill switch, like S2.
  `rebalance` is a no-op — the same-strike calendar nets near delta-flat, so it carries no band
  hedge.
- **Calendar parity (course p.45) is relied on, not re-derived.** The consistency identity is
  the surface layer's no-calendar-arbitrage condition — total variance is non-decreasing in
  maturity, `w(k, T_back) ≥ w(k, T_front)` at the shared strike (blueprint Eq. 21,
  `∂w/∂T ≥ 0`), enforced by `infra.surfaces.calendar_violations`. The S5 tests assert the spread
  the object builds respects it (no violation in the contango entry regime; a violation exactly
  when the term structure inverts — the kill regime), checking *against* the pricing layer's
  identity rather than re-implementing it.

## The research backtester (`backtest/`, course p.129-130 "2021-vs-2008")

`backtest/` is the **research backtester** (TARGET §5.7 / §7.8) — "does this idea have edge?".
It replays a strategy object over banked history day by day and produces the serious output the
spec demands: performance, drawdowns, turnover, exposure, Greeks, **stress losses**, and
**attribution through time** ("returns came from short vega and positive carry", not "Sharpe
1.4"). It is the *first* of §5.7's two machines; the **production shadow** ("would my live system
have produced this P&L?") is the deliberate second build and is **not** here (see the scope note
below).

**It reinvents no substrate.** The whole point of §5.7's "substrate genuinely ready" is that the
backtester is an orchestration + bookkeeping layer over already-landed compute:

- the strategy runs through the **same** §6 four-context harness (`run_strategy`,
  `context=BACKTEST`) paper/live use — so backtest and live cannot diverge in *how* the strategy
  is invoked (the production-shadow property, kept cheap for the second machine);
- each day's book is priced into landed `PositionRisk` lines (`infra.risk.greeks.position_risk`);
- the day-over-day P&L is decomposed by the landed realized attribution engine
  (`infra.risk.attribution.attribute_realized_book` → named delta/gamma/vega/theta/rho/vanna/volga
  terms + a residual honesty check) — the §5.7 "attribution through time" primitive, already built;
- the daily stress loss is the landed worst-case scenario (`infra.risk.scenarios.worst_case`).

The pieces:

- **`run_backtest(strategy, data, *, dates, config)` (`engine.py`)** — the day-by-day replay
  loop. Each day it rolls off expired contracts (S2's "one expires daily"), prices the book,
  attributes the overnight move (yesterday's start-of-day Greeks re-marked at today's market),
  runs the strategy, opens any entry, and records one `DayResult`. **No look-ahead by
  construction:** the only date source is the loop variable; every read is keyed to the current
  `as_of`; the attribution's start is strictly yesterday and its end strictly today. The
  `check-lookahead-bias` skill was run against it, and `test_backtest_no_lookahead.py` proves it
  mechanically with a recording data seam.
- **`BacktestData` / `InMemoryBacktestData` / `StoreBackedBacktestData` / `HeldContract`
  (`data.py`, `store_data.py`)** — the as-of market-state seam (the look-ahead boundary): the
  entry `SignalSnapshot`, plus the two valuation reads that pin a grid-coordinate leg to a fixed
  contract on its entry day (`concretize_leg`) and re-mark it each later day (`valuation`). The
  in-memory implementor is the hand-checkable test fixture; **`StoreBackedBacktestData` is the
  production path** — it reads the as-of `projected_option_analytics` cell for the leg's grid
  coordinate (`underlying`/`tenor_label`/`delta_band`/`surface_side`) on each `as_of`, pins the
  concrete contract identity (right + strike + expiry) from that row, and rebuilds the
  `ContractValuationInput` from the same row (spot = `forward_price` since the pipeline pins
  `carry == 0`, vol = `implied_vol`; multiplier/currency injected). It adds **no** compute, exactly
  as `StoreBackedDispersionData`/`StoreBackedGammaData` compose their `trade_date`-narrowed grid
  reads. The signal half reuses the landed `signal_snapshot_from_store` bridge.
- **`TransactionCostModel` (`costs.py`)** — the explicit cost model the engine charges at entry: a
  per-contract `commission_per_contract` plus a `slippage_rate` fraction of priced notional
  (`|unit price| × multiplier × contracts`). `BacktestConfig.costs` defaults to `NO_COST` (gross,
  byte-identical to before). The cost is charged on the **same** `as_of` the leg opens (no forward
  mark), so `DayResult.transaction_cost` / `cumulative_net_pnl` and the summary's
  `total_transaction_cost` / `total_net_pnl` are net of cost; gross P&L is unchanged so the two are
  comparable.
- **`reconcile_shadow` / `ShadowReport` (`shadow.py`)** — the **production-shadow** machine: it
  drives the **same** §6 `run_strategy` step (and the same `daily_entry_fires` predicate the
  research engine uses, capacity counted off the *booked* line) over the same dates, concretizes
  the intended legs through the same `BacktestData` seam, and diffs net-by-contract signed quantity
  against injected `BookedFill`s — flagging per-day drift between *what the one logic object would
  have traded* and *what was actually booked* (paper/live). The strategy layer can't import
  execution (it sits above), so `BookedFill` is a layer-neutral value the caller above execution
  (the BFF / an ops script) fills from the execution fills ledger. This is the "a strategy isn't
  real until backtest, paper, and live share one logic object" check made mechanical.
- **`BacktestResult` / `DayResult` / `BacktestSummary` (`results.py`)** — the output. `days` is the
  through-time table; `summary` rolls it up (total P&L, **net P&L**, **total transaction cost**,
  max drawdown, annualised Sharpe, turnover, worst stress loss — each one pure function);
  `cumulative_attribution()` is the §5.7 headline view: the named per-Greek P&L summed across the
  stretch, so *which Greek paid* is a number, not a story.

**First concrete target (§7.8):** S2, the index short-put line, replayed through a banked stretch
and an adverse (spot-down + vol-up) regime — the course's 2021-vs-2008 method industrialised. The
engine drives exactly S2's daily decision: `decide_sell` (signal ∧ capacity) for the add, the
capacity count read off the backtest book itself (the booked line *is* the book), the rolling
roll-off in the loop.

**BFF endpoint (`apps/frontend`):** `POST /api/backtest/run` launches a store-backed S2 backtest
and returns the full serious output in one call (no persisted backtest table — it is computed on
demand). Request: `index`, `reference_tenor`, `start_date`/`end_date` (the window is narrowed to
the days actually banked for the index), `provider`, a `put_line` config block, optional `costs`
and `stress_grid`. Response: `summary` (perf / net / cost / drawdown / Sharpe / turnover / worst
stress), `cumulative_attribution` (which Greek paid), and a `days` array (per-day open contracts,
realized + net P&L, transaction cost, stress loss, exposure Greeks). The Strategy/Backtest page
(F-STRAT) consumes this.

**Scope / out of scope:**

- **Research + production-shadow both land here.** The research machine ("does the idea have
  edge?") and the shadow ("did my live system match?") now both exist; the shadow stays cheap
  because the strategy is invoked through the *same* harness call.
- **The store-backed `BacktestData` lands here** (`StoreBackedBacktestData`) alongside the
  in-memory reference adapter (still the hand-checkable fixture, honouring "never smoke-test against
  canonical `data/`").
- **The transaction-cost model is explicit** (`TransactionCostModel`); with `NO_COST` the reported
  P&L is the gross upper bound exactly as before, and a configured model reports net alongside it.
- **Open follow-ups:** the shadow reconciles *constructed-vs-booked legs* (the drift that actually
  bit historically); a *P&L*-level shadow (live realized vs backtest realized on the same booked
  line) is the next depth. The store adapter assumes the pipeline's `carry == 0` (forward == spot)
  and a single multiplier/currency per index (injected) rather than a per-contract instrument-master
  join — true for the index-only universe today.

## Testing

From the repo root, the one gate:

```
uv run ruff check . && uv run mypy . && uv run lint-imports && uv run pytest -q
```

Strategy tests are in `tests/` and run as part of the suite (registered in the root
`pyproject.toml` `testpaths`). They cover the contract validation, the four-context invariance,
the exit/rebalance decisions over real `PositionRisk` lines, and the stamp's two seams
(composition layering + per-strategy grouping). The `Basket.strategy_id` round-trip lives in
`packages/infra/tests/test_contracts_validation.py`. The backtester suite
(`test_backtest_{results,book,engine,no_lookahead}.py`) derives every expected value
independently: the summary statistics by hand, the day P&L and attribution against the landed
pricer applied as an independent oracle (`(price(end) - price(start)) * scale`), and the
no-look-ahead guarantee by a recording-seam audit (not a claim).

## Known limitations / out of scope

- **S1, S2, S3 and S5 live here; S4 does not yet.** The spine
  (`contract`/`signals`/`strategy`/`harness`) is strategy-agnostic; `s1_dispersion.py` +
  `dispersion_data.py` (S1), `s2_put_line.py` (S2, config-only), `s3_gamma.py` +
  `gamma_data.py` (S3), and `s5_calendar_carry.py` (S5) are the concrete strategies so far. S4's
  construction/entry/exit rules are still owned by its S-task. The research backtester (`backtest/`) replays any of them; its
  first target is S2.
- **Backtester now covers research *and* production-shadow** — the store-backed data adapter
  (`StoreBackedBacktestData`), the explicit `TransactionCostModel`, the `reconcile_shadow`
  constructed-vs-booked drift check, and the `POST /api/backtest/run` BFF endpoint all land (see the
  backtester section above). The remaining depth is a P&L-level shadow (live realized vs backtest
  realized on the same booked line).
- **Signal computation is not here.** The strategy reads `SignalSnapshot`; the infra signal
  layer (`algotrading.infra.signals`) derives and persists it, and `signal_snapshot_from_store`
  bridges the two. A caller can still build a snapshot from any source (research/backtest);
  paper/live source it from the persisted `strategy_signals`.
- **Enforcement and booking are not here.** `decide_exit` *emits* a flatten; the execution
  kill switch *enforces* it, and the booker turns the emitted basket into fills (execution
  layer, above this one).
