# strategy-s5-calendar-carry — S5 front/back calendar carry on the term-structure slope (optional fifth)

> **Source:** TARGET §3 (S5 — calendar carry, course p.42–45, the **optional** fifth strategy).
> Entry reads the term-structure slope the front already renders; the p.45 parity identity is the
> consistency check the pricing layer already enforces.

## The gap
No strategy object (`packages/strategy` empty skeleton). The term-slope entry signal is owned by
the signal layer but unconsumed; no calendar-spread leg builder.

## Scope — the S5 strategy object (rules, not infra)
- **Construction:** short front-month / long back-month at the **same strike** — positive theta
  from the front decaying faster, long back vega.
- **Entry signal:** the **term-structure slope** (front/back, contango) from the signal layer
  ([[infra-signal-layer]]) — the front already renders the term-structure panel.
- **The strategy contract (§1/§3):** premium = front theta decays faster than back; intended
  Greeks = short front / long back vega, positive theta; kill = front-month event repricing (term
  structure inverts).
- **Consistency check:** the calendar parity identity (course p.45) is already enforced by the
  pricing layer — this object relies on it, does not re-derive it.

## Depends on / blocks
- [[infra-signal-layer]] (term-structure slope) + the index option chain capture across tenors
  (landed) + [[execution-fills-position-store]] (the calendar spread as a booked position) + 2A leg
  container.
- **Optional / lowest priority** of the §3 book — sequence after S1–S4 unless the owner re-prioritizes.

## Done criteria
An S5 strategy object builds the same-strike front/back calendar spread, emits an entry decision
from the term-structure slope, and exposes its named contract (premium/signal/Greeks/kill); the
same object is callable in research/backtest/paper/live (§6); unit-tested against the p.45 calendar
parity identity; gate green.
