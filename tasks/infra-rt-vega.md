# T-rt-vega — Running-Time Vega (annualised vega) per strike

> **Source:** course transcript req #5 (`ThomasHossen/MM_options_trading.md` §4 — the prof asks
> for RT-Vega **on each strike** alongside the standard vega). `documentation/` is gone; the
> canonical course reference is `ThomasHossen/MM_options_trading.md`.

## The gap
No `rt_vega` / `running_vega` / annualised-vega anywhere in `packages`. Not in `PricingResult`,
not in the projection grid.

## Scope
- Add **RT-Vega = vega annualised** (the time-normalisation of vega) as an output **per strike**,
  in the pricing/projection layer, carried through to the front beside vega — raw + cash, unit
  string attached (consistent with the dual-representation standard, ADR 0036).
- Confirm the annualisation convention against `TARGET.md` (the domain authority; absorbed the
  blueprint) and `ThomasHossen/MM_options_trading.md` before fixing the formula — do not invent the
  normalisation if the domain source settles it (absolute rule). `documentation/` is gone; ADR 0050
  records the resolved convention.

## Depends on / pairs with
Natural to land with [[infra-second-order-greeks]] (same pricing-output extension).

## Done criteria
RT-Vega per strike in `PricingResult`/projection, raw+cash with units, convention matches
`TARGET.md`/ADR 0050; oracle test; gate green.
