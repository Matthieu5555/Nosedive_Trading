# strategy-s1-dispersion — S1 dispersion strategy object (flagship): top-10 straddles vs short-forward index leg

> **STATUS: LANDED 2026-06-14** (branch `strategy-s1-dispersion`). The first concrete strategy on
> the spine ([strategy-contract-base](strategy-contract-base.md)) and the first consumer of the
> ADR-[0048](../../.agent/decisions/0048-per-side-vol-surfaces.md) per-side surfaces: each straddle
> routes its call leg to the call wing and its put leg to the put wing. `DispersionStrategy`
> (pure rules) + `StoreBackedDispersionData` (the as-of store I/O) in `packages/strategy`; entry on
> ρ̄ ≥ threshold, top-N point-in-time straddles + a delta-flattening synthetic short-forward index
> leg-pair, net-vega-collapse kill, band rebalance. v1 = forward-only (short delta), net long vol;
> v2 (short index straddle → pure correlation spread) deferred. Full gate green (1960 passed, 12
> skipped). The realized-correlation reading still arrives with [infra-signal-layer](../infra-signal-layer.md);
> the productionised band rule is [strategy-delta-hedge-band](../strategy-delta-hedge-band.md).

> **Source:** TARGET §3 (S1 — the owner's spec) + §0 universe model + §1 (the edge chain).
> The flagship strategy; the end-of-week strategy to enter (§2.3). One strategy object,
> four contexts (§6): research, backtest, paper, live call the same object.

## The gap
No strategy object exists anywhere — `packages/strategy` is an empty skeleton
(`algotrading.strategy.__init__`). The infra primitives S1 stands on are built (`risk/basket.py`
Eq-23 variance, `risk/multileg.py` book-additive risk, the underlying-generic analytics engine,
the parity forward), but nothing assembles them into the dispersion rule: pick legs, size the
hedge, name the harvested premium, declare the kill condition.

## Scope — the S1 strategy object (rules, not infra)
- **Construction (v1):** long ATM straddles on the **point-in-time top-10 SX5E constituents by
  index weight** (1A membership, never a hand-set list), and a **short index leg sized to flatten
  the basket's net dollar delta**. Until futures capture lands (1D, parked), the short leg is a
  **synthetic short forward from the index chain** (short call + long put, same strike/expiry),
  priced off put–call parity the pipeline already trusts.
- **Entry rule:** enter when implied correlation ρ̄ is **rich** — index ATM IV expensive relative
  to the constituent ATM IVs on the same tenor. ρ̄ comes from the signal layer; this object
  consumes it, it does not re-solve Eq 23.
- **Hedging discipline:** per-name delta drift re-flattened **by rule** (the delta-band rule,
  [[strategy-delta-hedge-band]]); the short-forward leg re-sized at each rebalance.
- **The strategy contract (§1/§3):** names the premium (correlation premium), the signal (ρ̄ rich),
  the intended Greeks (long single-name gamma/vega, ~0 net delta), and the kill condition (single
  names go quiet together — realized correlation ↑, single-name vol ↓, theta bleed).
- **v1 boundary:** v1 shorts the **forward** (delta only), stays net long vol. v2 (short index
  straddle leg → pure correlation spread) is explicitly out of scope here — natural upgrade once
  v1 attribution is trusted.

## Depends on / blocks
- [[ibkr-constituent-option-capture]] (per-name option chains/surfaces) — **hard blocker**, owned by
  the ibkr/capture layer; do not claim it here.
- [[infra-signal-layer]] (implied ρ̄ per tenor) — the entry signal; owned at the infra seam.
- [[strategy-delta-hedge-band]] (the re-hedge rule) + [[execution-fills-position-store]] (the booked
  position the rebalance mutates) + 2A `Basket`/`BasketLeg` (the leg container) + the synthetic
  short-forward builder (S1's own leg builder — specced inline here unless split out).
- Attribution (2C/§7.2) must show P&L in single-name gamma/vega, ~0 in net delta — the contract enforcer.

## Done criteria
An S1 strategy object resolves the point-in-time top-10, builds the straddle legs + the
delta-flattening synthetic-forward leg, exposes its named contract (premium/signal/Greeks/kill),
and emits an entry decision from ρ̄; the same object is callable in research/backtest/paper/live
(§6); top-10 is point-in-time and config-driven; unit-tested on a hand-built basket with attribution
showing the intended Greek profile; gate green.
