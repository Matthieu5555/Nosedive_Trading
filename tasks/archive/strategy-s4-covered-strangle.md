# strategy-s4-covered-strangle — S4 covered short strangle cycle on a fundamentally-held name

> **Source:** TARGET §3 (S4 — covered short strangle, course p.56–58). Decorrelated from S1–S3 by
> **driver**: its risk is idiosyncratic to a chosen holding, not to the vol complex.

## The gap
No strategy object (`packages/strategy` empty skeleton). The assignment-cycle accounting
(EPP/ESP, monthly roll states) has no implementation.

## Scope — the S4 strategy object (rules, not infra)
- **Construction:** on a name we fundamentally want to own (course rule: the long position requires
  a good fundamental story). Buy ¼–½ of the desired position; sell OTM put + OTM call at 30–45d
  with net Δ≈0.
- **The cycle, not a trade:** roll monthly in the middle state; **put assignment** averages in at
  `EPP = X − P₀ − C₀`; **call assignment** exits at `ESP = X + P₀ + C₀`. Implement the three
  states (middle-roll / put-assigned / call-assigned) and the entry/exit price accounting.
- **The strategy contract (§1/§3):** premium = range premium on a fundamental holding; intended
  Greeks = positive theta, ~0 entry delta, long stock; kill = big move either way in a name we
  chose to own.
- The fundamental-story gate is operator input (a flagged holding), not a computed screen — the
  object consumes the held name, it does not pick it.

## Depends on / blocks
- [[execution-fills-position-store]] (the multi-leg cycle is a booked, rolling position; assignment changes
  it) + constituent capture for the single-name underlying ([[ibkr-constituent-option-capture]], ibkr
  layer) + the index-constituent universe (the held name is a constituent, never a standalone
  underlying — TARGET §0).

## Done criteria
An S4 strategy object builds the covered strangle on an operator-flagged held name, runs the
monthly-roll cycle through the three assignment states with correct EPP/ESP accounting, and exposes
its named contract (premium/signal/Greeks/kill); the same object is callable in research/backtest/
paper/live (§6); unit-tested on the course p.56–58 EPP/ESP cycle; gate green.
