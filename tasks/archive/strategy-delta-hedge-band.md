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
Needed by the S1 dispersion book ([[ibkr-constituent-option-capture]] + [[infra-signal-layer]]) and S3.
Pairs with [[execution-fills-position-store]] (rebalances change the booked position).

## Done criteria
A config-driven band rule decides hold/re-hedge and sizes the hedge; used by S1/S3 strategy logic;
band width is typed config; unit-tested on the course's |Δ| cycle; gate green.

## Landed (2026-06-14)

`packages/strategy/src/algotrading/strategy/delta_hedge_band.py` — the shared rule, factored
out of any one strategy so S1/S3/S4 share **one** band decision:

- **`DeltaHedgeBand`** (frozen, validated) — `target` (the net delta the book hedges to; S1 is
  delta-flat by construction → 0), `half_width` (the **economic tolerance**, the only config
  input — ADR 0028, not a `.py` literal), and `hedge_ratio` (the hedge instrument's unit-delta
  convention; default −1 = neutralise in delta units). `target`/`hedge_ratio` are a strategy's
  structural choices; the tunable is `half_width`.
- **`decide_delta_hedge(net_delta, band)`** — pure: **holds** (zero quantity) while net delta is
  within `half_width` of `target`, and on band exit returns `hedge_ratio × (net_delta − target)`,
  the quantity that brings delta back to target. Returns a `HedgeInstruction` (signed quantity +
  `breached` + audit reason). This is the course's "don't pin delta continuously, it bleeds
  spread; re-hedge only on band exit" rule as one inspectable call.

**Wiring.** `DispersionStrategy.rebalance` (S1) now delegates to it with `DeltaHedgeBand(target=0,
half_width=config.delta_band)` — behaviour **byte-identical** to the prior inline check (the
existing S1 rebalance tests stay green unchanged); the duplicated inline rule is removed. S3
(p.108 gamma scalp, stock hedge) and S4 share the same rule when those tasks land — the rule
takes a `hedge_ratio`/`target` precisely so they need no second copy.

**Tests** (`tests/test_delta_hedge_band.py`, 14 cases): the course |Δ| cycle (hold inside the
band, re-hedge only on band exit, sizing checked by hand), edge inclusivity (exactly-representable
floats), the flat-book (S1) band around zero, zero-half-width continuous re-hedge, `hedge_ratio`
scaling, non-zero-target excess sizing, and the validation rejections (negative half-width, zero
ratio). Expected values derived independently from the course rule, never read back from the code.

**Gate green:** ruff ✓, mypy (239 files) ✓, import-linter ✓, pytest 2071 passed / 12 skipped.

Not done here (out of scope): wiring S3/S4 (their specs own that); a production YAML loader for
`DispersionConfig` (no loader exists yet — the band remains a typed config field, not a literal).
