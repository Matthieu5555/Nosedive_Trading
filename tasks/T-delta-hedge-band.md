# T-delta-hedge-band — band-based delta-hedge rebalancing (re-hedge only on band exit)

> **Source:** course transcript req #9 (§ "Delta-hedge en bande")
> (`documentation/transcripts/AlgoTradingCourse2-Greeks-et-strategies-vol.md`) + TARGET §3
> (S1 dispersion + S3 gamma trading both hedge by rule).

## The gap
No band-based hedge rule anywhere. The course is explicit: an ATM straddle has |Δ|≈0.5; do **not**
re-hedge continuously to pin |Δ|=0.5 (it bleeds cost at every step) — keep the position while |Δ|
stays **inside a band** (~0.455–0.46, i.e. ~±0.06 around target) and **re-hedge only on band exit**.

## Scope
- A reusable **delta-band rebalancing rule**: given a position's current net delta and a configured
  band (width is config, ADR 0028 — economic), decide hold vs re-hedge, and size the hedge leg
  (the index future / synthetic forward for S1; stock for S3).
- Used by S1 (per-name drift re-flattened by rule, future leg re-sized at each rebalance), S3
  (the p.108 scalp cycle), S4.
- The band edges live in **config**, not a `.py` literal.

## Depends on / blocks
Needed by the S1 dispersion book ([[T-constituent-option-capture]] + [[T-signal-layer]]) and S3.
Pairs with [[T-fills-position-store]] (rebalances change the booked position).

## Done criteria
A config-driven band rule decides hold/re-hedge and sizes the hedge; used by S1/S3 strategy logic;
band width is typed config; unit-tested on the course's |Δ| cycle; gate green.
