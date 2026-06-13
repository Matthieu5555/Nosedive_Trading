# T-rt-vega — Running-Time Vega (annualised vega) per strike

> **Source:** course transcript req #5
> (`documentation/transcripts/AlgoTradingCourse2-Greeks-et-strategies-vol.md` §4). The prof asks
> for RT-Vega **on each strike** alongside the standard vega.

## The gap
No `rt_vega` / `running_vega` / annualised-vega anywhere in `packages`. Not in `PricingResult`,
not in the projection grid.

## Scope
- Add **RT-Vega = vega annualised** (the time-normalisation of vega) as an output **per strike**,
  in the pricing/projection layer, carried through to the front beside vega — raw + cash, unit
  string attached (consistent with the dual-representation standard, ADR 0036).
- Confirm the annualisation convention against the blueprint math notes
  (`documentation/blueprint/05-math-notes.md`) before fixing the formula — do not invent the
  normalisation if the blueprint settles it (absolute rule).

## Depends on / pairs with
Natural to land with [[T-second-order-greeks]] (same pricing-output extension).

## Done criteria
RT-Vega per strike in `PricingResult`/projection, raw+cash with units, convention matches the
blueprint; oracle test; gate green.
