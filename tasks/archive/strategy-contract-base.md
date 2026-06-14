# strategy-contract-base — the shared Strategy spine: typed contract (premium/signal/Greeks/kill), decision interface, one-logic-four-contexts

> **Source:** TARGET §1 ("a strategy here is a contract: it names the premium it harvests, the
> signal that triggers it, the Greeks it intends to hold, and its kill condition") + §3 (the book
> table — those four columns *are* the contract) + §6 ("One logic, four contexts. Research,
> backtest, paper, and live call the same strategy object"). This is the **foundation** spec for the
> `strategy-` lane: the spine every S-task and the backtester assume but none builds.

## The gap
`packages/strategy` is an empty skeleton (`algotrading.strategy.__init__`, "Imports infra/core
only"). Grep confirms **zero** `Strategy` class/protocol, no `kill_condition` / `intended_greeks` /
entry-decision type anywhere in `packages/` or `apps/`. Yet **all five** strategy specs
([[strategy-s1-dispersion]], [[strategy-s2-index-put-line]], [[strategy-s3-gamma-trading]],
[[strategy-s4-covered-strangle]], [[strategy-s5-calendar-carry]]) open with "one strategy object,
four contexts" and each lists "the strategy contract (§1/§3): names premium / signal / intended
Greeks / kill" — and [[strategy-backtester]] must "call the same strategy object." Five leaves and
the backtester depend on a trunk that has no spec. Build it once here or it gets reinvented five
incompatible ways.

The two seams that fall out of the missing trunk are also unowned today:
- **Strategy → composition.** `infra/risk/book.py` (2D, [[strategy-composition]]) composes resolved
  **risk lines / 2A baskets**, never a `Strategy` object (verified: `book.py` layers `PositionRisk`
  lines). Nothing names "a strategy resolves to the 2A position set the book layers."
- **Strategy → attribution.** TARGET §5.2 + §7.2 want P&L attribution **per strategy**, but there is
  no `strategy_id` on positions/fills (verified: no `strategy_id` in contracts/attribution). Without
  a strategy identity stamped on what a strategy emits, per-strategy grouping has nothing to group on.

## Scope — the spine only (NOT any individual strategy's rules)
1. **The typed `StrategyContract`** in `packages/strategy` — a frozen, typed record of the four §3
   columns: `premium_harvested` (the named premium), `signal` (what triggers entry), `intended_greeks`
   (the Greek profile the position is *supposed* to hold — the thing attribution checks against), and
   `kill_condition` (the declared death mode). This is the object §1 calls "a contract"; it is data,
   inspectable and testable, one per strategy.
2. **The `Strategy` protocol / base** — the minimal interface every S1–S5 object implements:
   - `contract` → its `StrategyContract`.
   - an **entry decision** from a signal input (`(as_of, signals) → enter / hold / no-op`), consuming
     the [[infra-signal-layer]] outputs — the object *reads* ρ̄ / IV-rank / RV−IV / term-slope, it does
     not compute them.
   - an **exit / kill decision** (`(as_of, state, market) → flatten / roll / hold`) that fires the
     declared `kill_condition`; the object *emits* the decision, [[execution-operational-hardening]]'s
     kill switch *enforces* it (cross-lane seam, not built here).
   - a **construction** step that emits the legs as a **2A `Basket` / `BasketLeg`** position set
     (the leg container the whole book already speaks), **stamped with the strategy's identity**
     (see seam below) — so the same emitted object flows into 2D composition and attribution.
   - an optional **rebalance hook** that delegates to the shared [[strategy-delta-hedge-band]] rule.
3. **The strategy-identity stamp (composition + attribution seam).** Define a `strategy_id` /
   `strategy_label` carried on the emitted position set so (a) 2D ([[strategy-composition]]) can layer
   strategies as named book layers and (b) attribution ([[infra-pnl-attribution]], §7.2 per-strategy
   grouping) can group P&L by strategy and *enforce the contract* (P&L must land in `intended_greeks`,
   residual elsewhere). Add the field via the additive contract-evolution path; do not fork the
   `Basket`/`PositionRisk` contracts — extend them minimally. Coordinate the field name with the
   attribution lane (this spec defines it; attribution consumes it).
4. **One-logic-four-contexts harness (§6).** A thin calling convention so research, backtest, paper,
   and live invoke the *same* `Strategy` instance — the object takes injected market/signal state and
   returns decisions + a position set; the context (notebook / backtester / paper booker / live)
   supplies the state and consumes the decisions. No strategy logic lives in the context. This is the
   contract [[strategy-backtester]]'s "production shadow" relies on to prove research == paper == live.

## Depends on / blocks
- **Depends on:** 2A `Basket`/`BasketLeg` + `risk/multileg.py` (landed) and the `infra/risk` contracts
  it stamps; [[infra-signal-layer]] for the *type* of the entry signal input (can stub the signal type
  ahead of that lane landing — the protocol is buildable now, decisions go live when signals land).
- **Blocks (the whole reason it is first):** [[strategy-s1-dispersion]], [[strategy-s2-index-put-line]],
  [[strategy-s3-gamma-trading]], [[strategy-s4-covered-strangle]], [[strategy-s5-calendar-carry]] (each
  implements this protocol), [[strategy-backtester]] (calls the same object), and the **per-strategy**
  half of [[infra-pnl-attribution]] (needs the `strategy_id` stamp).
- **Not here:** any individual strategy's construction/entry rules (the S-tasks own those); signal
  computation (infra); booking/fills/kill-switch enforcement (execution); the 2D book aggregation
  (already landed in `book.py` — this spec gives it a *named strategy layer* to consume).

## Done criteria
A typed `StrategyContract` (premium / signal / intended Greeks / kill) and a `Strategy` protocol live
in `packages/strategy` (imports infra/core only, layering green); the protocol defines entry, exit/
kill, construction-to-a-stamped-2A-position-set, and an optional band-rebalance hook; a `strategy_id`
stamp rides the emitted position set via the additive contract path and is consumed by both 2D
composition and per-strategy attribution; one toy reference `Strategy` (not S1–S5 — a trivial
fixture) proves the same instance runs unchanged across the four-context harness and that its emitted
position set composes in `book.py` and groups in attribution; unit + contract + seam tests per
[[TESTING.md]]; gate green (`uv run ruff … && mypy … && lint-imports && pytest`).
