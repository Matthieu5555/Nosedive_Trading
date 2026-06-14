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
  net dollar-vega collapses (the long-vol thesis gone); `rebalance` re-flattens net delta by band.
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

## Testing

From the repo root, the one gate:

```
uv run ruff check . && uv run mypy . && uv run lint-imports && uv run pytest -q
```

Strategy tests are in `tests/` and run as part of the suite (registered in the root
`pyproject.toml` `testpaths`). They cover the contract validation, the four-context invariance,
the exit/rebalance decisions over real `PositionRisk` lines, and the stamp's two seams
(composition layering + per-strategy grouping). The `Basket.strategy_id` round-trip lives in
`packages/infra/tests/test_contracts_validation.py`.

## Known limitations / out of scope

- **S1 lives here; S2–S5 do not yet.** The spine (`contract`/`signals`/`strategy`/`harness`) is
  strategy-agnostic; `s1_dispersion.py` + `dispersion_data.py` are the first concrete strategy.
  S2–S5's construction/entry/exit rules are still owned by their S-tasks.
- **Signal computation is not here.** The strategy reads `SignalSnapshot`; the infra signal
  layer (`algotrading.infra.signals`) derives and persists it, and `signal_snapshot_from_store`
  bridges the two. A caller can still build a snapshot from any source (research/backtest);
  paper/live source it from the persisted `strategy_signals`.
- **Enforcement and booking are not here.** `decide_exit` *emits* a flatten; the execution
  kill switch *enforces* it, and the booker turns the emitted basket into fills (execution
  layer, above this one).
