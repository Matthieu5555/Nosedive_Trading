# strategy-decorrelation-analytics — verify the book is decorrelated: cross-strategy P&L correlation, factor overlap, shared-tail, marginal risk

> **Source:** TARGET §5.8 (portfolio construction — "five independent sources of P&L, diversified
> by failure mode") + §3 ("the decorrelation claim is **verified, not assumed**; two differently-
> named strategies can secretly be the same trade"). The analytic 2D deliberately does **not** do.

## The gap
2D ([[2D-strategy-composition]]) composes and **additively aggregates** what the operator picks —
"decorrelated" there is operator *intent*, and an optimiser is explicitly guarded out. So nothing
yet **measures** whether the composed book is actually diversified. §3 names the test data: S1/S3
share a failure mode (low realized vol) **on purpose** so the book view must prove it can see the
overlap — that proof has no home.

## Scope — read-only diagnostics over a composed book (not an optimiser)
- **Cross-strategy P&L correlation** across the book's sub-strategies (the realized + stressed P&L
  series 2D/2C already produce).
- **Shared-tail overlap** — do two layers lose in the same stress nodes (the 2B grid the book
  already reprices over) / the same named scenarios.
- **Factor overlap** — which sub-strategies load on the same Greek/factor exposure (e.g. S1 and S3
  both short realized-vol-low).
- **Marginal contribution to risk and Sharpe** per sub-strategy; the admission question (§5.8):
  "does adding this improve the portfolio after costs, capacity, drawdown interaction?".
- All of it **read-only** — surfaces numbers, never reweights/drops/reorders layers (the same
  out-of-scope boundary 2D's `test_no_decorrelation_optimiser` guards).

## Depends on / sequence
- [[2D-strategy-composition]] (the composed book + combined PnL surface + per-layer breakdown) —
  this layers diagnostics **on top of** 2D's frozen book contract; depends on it landing first.
- The §3 strategy book (S1–S5 objects) for real test data; banked realized P&L for the realized
  correlation (vs the stressed-only view). **Post-week per §5.8** — not a this-week deliverable.

## Done criteria
Over a composed 2D book, the diagnostics surface cross-strategy P&L correlation, shared-tail
overlap, factor overlap, and marginal contribution to risk/Sharpe; the S1/S3 shared-failure-mode
overlap is **visibly detected** on test data; all read-only (composing/diagnosing never mutates a
layer); no optimiser introduced; gate green.
