# strategy-s3-gamma-trading — S3 delta-neutral gamma-positive scalp on one cheap name

> **Source:** TARGET §3 (S3 — gamma trading, course p.107–108) + §3 (S1/S3 shared failure mode,
> the book view must prove it can see the overlap).

## The gap
No strategy object (`packages/strategy` empty skeleton). The delta-band scalp cycle (course p.108)
has no implementation; the IV-rank entry signal is owned by the signal layer but unconsumed.

## Scope — the S3 strategy object (rules, not infra)
- **Construction:** on **one** constituent name whose vol is cheap, long call + short stock
  (or long put + long stock) to Δ=0. Entry ranking (course): best = low IV expected to rise;
  worst = high IV about to fall.
- **Entry signal:** **IV rank / percentile per name** (course p.36) — consumed from the signal
  layer ([[infra-signal-layer]]), needs banked IV history as raw material.
- **The scalp cycle (p.108):** rebalance in **delta bands** — sell strength in clips as delta
  rises, buy back lower; each round trip banks the rectangle. Uses the shared band rule
  ([[strategy-delta-hedge-band]]).
- **The strategy contract (§1/§3):** premium = realized vol > implied on one cheap name; P&L =
  scalp gains − theta, vega the kicker/killer; intended Greeks = long gamma, delta-neutral by
  rule; kill = quiet drift + IV crush (gain < theta).
- **Shared failure mode with S1** (low realized vol) is intentional — held so the book view
  (2D / §5.8 correlation) must surface the overlap; do not "fix" the overlap here.

## Depends on / blocks
- [[infra-signal-layer]] (IV rank per name) + banked IV history depth + [[strategy-delta-hedge-band]]
  (the scalp band rule) + [[execution-fills-position-store]] (the scalped position) + constituent capture
  for the single-name underlying ([[ibkr-constituent-option-capture]], ibkr layer).

## Done criteria
An S3 strategy object selects one cheap name from IV rank, builds the Δ=0 long-gamma structure,
runs the band-based scalp cycle, and exposes its named contract (premium/signal/Greeks/kill); the
same object is callable in research/backtest/paper/live (§6); unit-tested on the course's p.108
scalp cycle; gate green.
