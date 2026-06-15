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

`infra` reads the stamp as an opaque label; it never reads strategy *logic*. See
[ADR 0046](../../.agent/decisions/0046-strategy-spine-contract-protocol-and-identity-stamp.md).

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
implement the `Strategy` protocol for real, and the first consumer of the ADR-0048 per-side
vol surfaces. It harvests the **correlation premium**: when index ATM IV is rich relative to
the constituent ATM IVs on the same tenor (high implied correlation ρ̄), a book that is long
single-name vol and short the index monetises the gap as the names decorrelate.

- **`DispersionStrategy`** — pure over an injected `DispersionConfig` (the economic parameters:
  `index`, `top_n`, `straddle_tenor`, `entry_threshold`, …, sourced from typed platform config,
  never `.py` literals) and a `DispersionMarketData` (the as-of I/O seam). Entry fires when ρ̄ ≥
  the threshold; `construct` builds a long ATM straddle on each **point-in-time top-N
  constituent** (resolved through `top_n_by_weight` — never a hand-set list), routing the
  **call leg to the call wing and the put leg to the put wing** (ADR 0048), plus a **synthetic
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
    routed to the **put wing** (ADR 0048). `decide_entry` (the protocol method the §6 harness
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
  **long ATM call** on that single name (routed to the call wing, ADR 0048) plus a **short stock
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
- **`BacktestData` / `InMemoryBacktestData` / `HeldContract` (`data.py`)** — the as-of market-state
  seam (the look-ahead boundary): the entry `SignalSnapshot`, plus the two valuation reads that
  pin a grid-coordinate leg to a fixed contract on its entry day (`concretize_leg`) and re-mark it
  each later day (`valuation`). The in-memory implementor is the hand-checkable test fixture; the
  **store-backed** implementor (the production path) composes execution's landed grid-cell
  concretizer + the infra valuation join exactly as `StoreBackedDispersionData` composes its
  reads — a documented follow-up, see below.
- **`BacktestResult` / `DayResult` / `BacktestSummary` (`results.py`)** — the output. `days` is the
  through-time table; `summary` rolls it up (total P&L, max drawdown, annualised Sharpe, turnover,
  worst stress loss — each one pure function); `cumulative_attribution()` is the §5.7 headline
  view: the named per-Greek P&L summed across the stretch, so *which Greek paid* is a number, not
  a story.

**First concrete target (§7.8):** S2, the index short-put line, replayed through a banked stretch
and an adverse (spot-down + vol-up) regime — the course's 2021-vs-2008 method industrialised. The
engine drives exactly S2's daily decision: `decide_sell` (signal ∧ capacity) for the add, the
capacity count read off the backtest book itself (the booked line *is* the book), the rolling
roll-off in the loop.

**Scope / out of scope for v1 (research):**

- **Research machine first; production shadow second.** This is "does the idea have edge?". The
  production-shadow reconciliation ("did my live system match?") is the deliberate second build,
  not here — but the design keeps it cheap because the strategy is invoked through the *same*
  harness call.
- **The store-backed `BacktestData` is the documented follow-up.** v1 ships the protocol + the
  in-memory reference adapter (so the engine and the landed risk/attribution are tested against
  hand-derived numbers without a store, honouring "never smoke-test against canonical `data/`").
  The store-backed implementor wires the landed concretizer + valuation join over a
  `trade_date`-narrowed grid read; it adds no compute, exactly like the S1/S3 store adapters.
- **No explicit transaction-cost / slippage model in the engine.** The fill mark is the data
  adapter's (the store-backed one uses the ADR-0043 concretizer's mark); explicit commission /
  slippage is a follow-up, so v1's reported P&L is a gross upper bound on net.

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

- **S1, S2 and S3 live here; S4/S5 do not yet.** The spine
  (`contract`/`signals`/`strategy`/`harness`) is strategy-agnostic; `s1_dispersion.py` +
  `dispersion_data.py` (S1), `s2_put_line.py` (S2, config-only), and `s3_gamma.py` +
  `gamma_data.py` (S3) are the concrete strategies so far. S4/S5's construction/entry/exit rules
  are still owned by their S-tasks. The research backtester (`backtest/`) replays any of them; its
  first target is S2.
- **Backtester scope is research-only in v1** — the production-shadow machine, the store-backed
  data adapter, and an explicit transaction-cost model are documented follow-ups (see the
  backtester section above).
- **Signal computation is not here.** The strategy reads `SignalSnapshot`; the infra signal
  layer (`algotrading.infra.signals`) derives and persists it, and `signal_snapshot_from_store`
  bridges the two. A caller can still build a snapshot from any source (research/backtest);
  paper/live source it from the persisted `strategy_signals`.
- **Enforcement and booking are not here.** `decide_exit` *emits* a flatten; the execution
  kill switch *enforces* it, and the booker turns the emitted basket into fills (execution
  layer, above this one).
