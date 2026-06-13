# T-second-order-greeks — emit Vanna/Volga/Charm (raw + cash), then complete attribution

> **Source:** TARGET §7.2 + §5.1/§5.2; course transcript req #3 + #8
> (`documentation/transcripts/AlgoTradingCourse2-Greeks-et-strategies-vol.md`).
> **Highest-leverage gap:** it unblocks BOTH attribution completion and the course's
> 2nd-order requirement. Pricing emission is the prerequisite — attribution cannot add terms
> the pricer does not produce.

## The gap
`PricingResult` (`packages/infra/src/algotrading/infra/contracts/tables.py`) emits
delta/gamma/vega/theta/rho only — no vanna/volga/charm anywhere in `infra/src`.
`risk/attribution.py` (2C, landed) decomposes Δ/Γ/Vega/Θ + residual on a **scenario shock** only.

## Scope
1. Pricing: add **Vanna, Volga, Charm** (and the obvious 2nd-order set) to the Black-76 engine
   output, in **raw decimal AND cash** (€/$ per underlying, ADR 0036 monetization), with unit
   strings — same dual representation as the 1st-order Greeks.
2. Attribution: extend `risk/attribution.py` with **Rho, Vanna, Volga** terms and
   **realized day-over-day** dPnL attribution (today it's scenario-shock only). Residual stays
   measured against the full reprice (the honesty meter).
3. Carry the new Greeks through the contract → projection → BFF → front term-structure panels.

## Depends on / blocks
Rho term pairs with [[T-rates-curve-ingest]] (R1) for the curve it bumps. Blocks the §7.2
"attribution completion" and the transcript's "résidu en 2e ordre".

## Done criteria
Vanna/Volga/Charm in `PricingResult` (raw+cash, units); attribution carries Rho/Vanna/Volga +
realized dPnL; residual shrinks on the golden case; tolerances + look-ahead clean; gate green.
