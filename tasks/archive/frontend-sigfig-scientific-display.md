# T-sigfig-scientific-display — render Greeks at ≥6 sig-figs in scientific notation on the front

> **Source:** course transcript req #6 (`ThomasHossen/MM_options_trading.md` §5 — Delta is O(1)
> while vega/gamma are O(0.1–0.001); a uniform 2-decimal rounding destroys the small Greeks).
> `documentation/` is gone; `ThomasHossen/MM_options_trading.md` is the canonical course reference.

## The gap
The analytics store Greeks at full float precision, but the front formatting (`lib/format.ts`)
does not guarantee **≥6 significant figures in scientific notation** — small Greeks lose their
information when rendered. No task owns this requirement (`T-front-currency-and-bands` touches
`format.ts` but for currency/bands, not sig-figs).

## Scope
- A front number formatter that renders Greeks (and other small-magnitude analytics) at **≥6
  significant figures, scientific notation** (mantissa × 10⁻ⁿ) where the magnitude warrants it,
  so each Greek keeps its useful information regardless of order of magnitude.
- Apply across the dollar-Greeks tables / term-structure panels. Coordinate with
  [[T-front-currency-and-bands]] (both touch `format.ts` — shared-tree hazard, agree ownership).

## Done criteria
Greeks render at ≥6 sig-figs / scientific where magnitude warrants; small Greeks no longer
collapse to 0.00; vitest covers the formatter; eslint/tsc green.
