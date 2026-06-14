# 0046 — The Strategy spine: typed contract, `Strategy` protocol, and the `strategy_id` identity stamp

- **Status:** accepted, 2026-06-14. Lands the strategy foundation
  ([`../../tasks/strategy-contract-base.md`](../../tasks/strategy-contract-base.md)) in
  `packages/strategy`, plus one additive field on the `infra` `Basket` contract.
- **Date:** 2026-06-14.
- **Implements:** TARGET §1 ("a strategy is a contract: it names the premium it harvests, the
  signal that triggers it, the Greeks it intends to hold, and its kill condition"), §3 (the
  strategy-book table — those four columns *are* the contract), and §6 ("one logic, four
  contexts: research, backtest, paper, and live call the same strategy object").
- **Relates to:** the 2A `Basket`/`BasketLeg` contract (the leg container a strategy emits) and
  `risk/multileg.py`; [[0006-risk-engine]] / `risk/book.py` (2D composition layers the emitted
  set as a named book layer); [[0038-by-greek-pnl-attribution-and-decomposition-conventions]]
  (per-strategy P&L grouping, TARGET §7.2, keys off the stamp); the infra **signal layer** lane
  (publishes the `SignalSnapshot` the strategy reads; not built here).

## Context

`packages/strategy` was an empty skeleton. All five strategy specs (S1–S5) and the backtester
open with "one strategy object, four contexts" and "the strategy contract: premium / signal /
intended Greeks / kill" — a trunk they all assume but none defines. Built five times it would
diverge five incompatible ways. Two cross-lane seams also fell out of the missing trunk and were
unowned: nothing named "a strategy resolves to the 2A position set the book layers," and there
was no strategy identity on what a strategy emits for per-strategy attribution to group on.

## Decision

Three choices a later agent would otherwise reverse-engineer.

**1. The four §3 columns are a frozen, typed `StrategyContract`, not prose.** `premium_harvested`
and `kill_condition` are free text (the human-named economic thesis), but `signal` is a closed
`SignalKind` enum (the §3 entry triggers: implied correlation, IV-vs-realized, IV rank, term
slope, range premium) and `intended_greeks` is `IntendedGreeks` of signed `GreekSign`
directions (long/short/flat per Greek). The *sign*, not a magnitude, is the testable contract:
attribution checks that realized P&L lands in the intended Greeks (TARGET §5.2), and a magnitude
is a sizing/construction concern the S-tasks own. Higher-order terms (vanna/volga/charm) are not
declared — they live in the residual the contract does not promise.

**2. `Strategy` is a structural `Protocol`, not a base class, and every method is pure.** S1–S5
implement it by shape with no inheritance coupling. The harness (`run_strategy`) adds no
decision logic: it routes injected `SignalSnapshot` / `MarketState` into the strategy's pure
methods and collects the results. Purity is the whole mechanism behind "research == backtest ==
paper == live" — the same instance fed equal state returns an equal `StrategyStep` in all four
`StrategyContext`s, which is the production-shadow property the backtester relies on. The context
is a label that rides on the result; the strategy can never branch on it.

**3. The identity stamp rides the emitted `Basket` as an additive-nullable `strategy_id`.** A
strategy's `construct` emits a 2A `Basket` carrying `strategy_id == contract.strategy_id`. We add
`strategy_id: str | None = None` to the existing `infra` `Basket` contract via the
additive-nullable evolution path (the same pattern as `dollar_theta`/`dollar_rho`): `None` on an
operator-authored or pre-strategy-layer basket, set on a strategy-emitted one, validated
non-empty when present. This is consumed by **both** 2D composition (the stamp keys
`BookLayerInput.label`, so a strategy is a *named* book layer) and per-strategy attribution
(group by `strategy_id`, check against `intended_greeks`). The harness refuses a basket whose
stamp does not match the strategy (`UnstampedBasketError`), so an unstamped set is caught at the
seam rather than flowing unnamed into composition/attribution.

## Alternatives rejected

- **A `Strategy` ABC.** Rejected: it couples five leaves to a base-class import for no behaviour;
  a `Protocol` gives the same type-checking with zero inheritance.
- **A new `StrategyBasket` subtype or a parallel position type for the stamp.** Rejected: the
  spec is explicit — do not fork `Basket`/`PositionRisk`, extend minimally. An additive optional
  field keeps every existing basket valid unchanged and reuses the whole 2A pricing/serialization
  path; a subtype would split the book's leg container in two.
- **Stamping `PositionRisk` instead of `Basket`.** Rejected: the strategy emits a `Basket` (the
  pre-priced position set); `PositionRisk` is the priced line the actor resolves downstream. The
  identity belongs on what the strategy *emits*. Composition already labels its layers and
  attribution already groups by `portfolio_id`; the stamp on the emitted set is what seeds both.

## Consequences

- S1–S5 and the backtester build on one interface; the stamp gives composition a named layer and
  attribution a grouping key. `infra` reads the stamp as an opaque label and never reads strategy
  logic — the layering contract (`infra` blind to alpha) stays intact (import-linter green).
- The `SignalSnapshot` type is defined here as the agreed shape; when the infra signal layer
  lands its contract, `SignalSnapshot` becomes a thin alias and consuming strategy code does not
  change. Decisions are buildable now; they go live when signals land.
- Enforcement is out of scope: `decide_exit` *emits* a flatten, the execution kill switch
  *enforces* it; `construct` emits a basket, the booker turns it into fills.
