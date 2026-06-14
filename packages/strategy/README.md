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
object identically. It is the spine only — no individual strategy's rules live here; the S-tasks
own those.

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

- **No strategy logic.** S1–S5's construction/entry/exit rules are owned by the S-tasks; this is
  the shared shape only.
- **Signal computation is not here.** The strategy reads `SignalSnapshot`; the infra signal
  layer derives it. Until that lane lands, callers build the snapshot from their own source.
- **Enforcement and booking are not here.** `decide_exit` *emits* a flatten; the execution
  kill switch *enforces* it, and the booker turns the emitted basket into fills (execution
  layer, above this one).
