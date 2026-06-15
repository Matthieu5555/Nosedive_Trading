# 0050 — RT-Vega (running-time / annualised vega) is `vega / sqrt(T)`

- **Status:** accepted, 2026-06-15.
- **Date:** 2026-06-15.
- **Relates to:** [[0036-dollar-greek-units-and-monetization-conventions]] (RT-Vega is carried
  in the same raw + dollar dual representation, with an explicit unit string), TARGET §7.2
  (the second-order Greek extension this rides alongside). Task: `tasks/infra-rt-vega.md`
  (course transcript req #5 — RT-Vega on each strike beside the standard vega).

## Context

Raw Black-Scholes/Black-76 vega is `vega = S · N'(d1) · sqrt(T) · e^{(b-r)T}` (per 1.00 of
vol in this engine's units; see `pricing/state.py`). Because vega scales with `sqrt(T)`, a
short-dated and a long-dated strike with the *same* vol sensitivity per unit of running
time report very different raw vegas — the longer tenor always looks "more vega" purely
because more calendar time remains. That makes raw vega **not comparable across maturities**,
which is exactly what the course asks to fix: it wants a vega figure that can be read
straight across the tenor grid.

The task spec pointed at `documentation/blueprint/05-math-notes.md` to settle the
annualisation convention. That documentation tree is dead (per the standing note), so the
convention is settled here from standard options theory, which is unambiguous on this point.

## Decision

**RT-Vega = `vega / sqrt(T)`**, in the pricer's own vega units (per 1.00 of vol).

Equivalently this is the maturity-independent core of vega:

```
RT-Vega = vega / sqrt(T) = S · N'(d1) · e^{(b-r)T}
```

The `sqrt(T)` factor is stripped, leaving the part that does **not** mechanically grow with
remaining time, so RT-Vega is directly comparable across tenors. This is the standard
"running-time" / "annualised" vega normalisation; it is the established convention and is
not invented here.

We fix it **as `vega / sqrt(T)` against the pricer's own emitted `vega`** (rather than
re-deriving `S · N'(d1) · e^{(b-r)T}` independently) so that, by construction, RT-Vega
carries the *same* unit and the *same* `e^{(b-r)T}` carry/discount convention as the engine's
vega — a unit or carry change to vega moves RT-Vega with it, and the two can never silently
disagree. The independent-oracle test pins this both ways: RT-Vega equals the engine's
`vega / sqrt(T)`, **and** equals an independently hand-built `S · N'(d1) · e^{(b-r)T}`.

**Boundary `T -> 0`.** `sqrt(T)` -> 0 would divide by zero. In the degenerate regime
(`maturity_years <= 0` or `sigma <= 0`) the engine already returns `vega = 0` (the
discounted-intrinsic branch). RT-Vega is **defined to be `0.0`** there — a guard, not a
`0/0`. This is the right limit: at expiry there is no vol sensitivity at all, so the
time-normalised one is zero too. The guard is on `maturity_years <= 0.0`, mirroring the
existing degenerate handling, and is tested explicitly.

**Dollar representation.** Dollar RT-Vega mirrors Dollar Vega exactly (ADR 0036):

```
RT-Vega$ = rt_vega · 0.01 · mult · qty        unit: "$ per 1 vol point"
```

i.e. the dollar value change for a one-vol-point (0.01) move, of the time-normalised vega.
It carries no convention fork (like Vega$, Vanna$, Volga$), so its unit is fixed and looked
up in `UNIT_STRINGS`, not stored as a field.

## Consequences

- `PriceGreeks` gains `rt_vega` (closed-form Black-76 fills it; the American lattice leaves
  it `0.0` — a documented gap, same as the other second-order Greeks).
- `PricingResult` and `ProjectedOptionAnalytics` gain `rt_vega` (raw) and `dollar_rt_vega`
  (cash), both **additive-nullable** (`float | None`) for the schema-evolution discipline of
  ADR 0036/0029 — a partition written before this lane reads them back `None`.
- The BFF emits `rt_vega` as a `{raw, dollar, unit}` metric beside `vega`, never a bare
  float (the ADR 0036 boundary rule).
- The convention is now pinned in code (this ADR + the `rt_vega` docstrings on
  `PriceGreeks`/`pricing/state.py` and `dollar_greeks.py`) and pinned by an
  independent-oracle test, so the formula is never implicit.
