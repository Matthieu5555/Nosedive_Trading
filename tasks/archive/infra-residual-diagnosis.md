# infra-residual-diagnosis — name the unmodeled exposure behind the attribution residual

> **Source:** TARGET §5.2 (the residual "is not only a gate — it is the next signal") + §7 #10
> (the destination row). **DEFERRED / DO NOT START YET** — sequenced after the booking chain
> (§7 #1) and **a week-plus of banked *realized* P&L**. This is the one place attribution crosses
> from deterministic decomposition into **statistical inference**, so it carries the full §6
> quant-guard bar (as-of, out-of-sample, no data-snooping) that the closed-form terms do not.

## The gap
Realized day-over-day attribution landed (`risk/attribution.py` — `attribute_realized_line/_book`,
`RealizedMove`, residual vs the full reprice). Today that residual is a **scalar stop-light**: large
→ "we don't understand our book" → cut. TARGET §5.2 wants it **diagnosed**: once realized dPnL is
decomposed against every term we price deterministically (through Volga), the leftover is the part
the Greek model cannot name — and that leftover is itself data.

There is **no task, anywhere, for the diagnosis step.** It is named infra ground (§7 #10) with zero
coverage. This spec gives it a home so it stops being invisible — it is not week work.

## Scope (when it opens)
- Persist the realized-attribution **residual time series** per book/strategy as-of, alongside the
  named terms it is the remainder of (the raw material; needs banked realized P&L depth).
- Regress the residual against candidate **unmodeled exposures**: skew/vanna dynamics, liquidity &
  execution slippage, jump/gap risk, vol-of-vol, regime. Name *which* exposure the book silently
  carries; feed it back into the surface/Greek model.
- **Quant-guard bar (mandatory, §6):** out-of-sample / walk-forward, no data-snooping, as-of
  everywhere (`check-lookahead-bias`). This is research-grade inference, not a closed-form term —
  the bar is higher than for the deterministic decomposition.

## Depends on / sequence
Hard prerequisites: §7 #1 (booking chain → fills-based position store → real realized P&L) **and**
a week-plus of banked realized attribution. Consumes the landed realized-attribution residual
(archived [infra-second-order-greeks](archive/infra-second-order-greeks.md) step 2,
[infra-pnl-attribution](archive/infra-pnl-attribution.md)). Pairs with the signal layer
[[infra-signal-layer]] (regime/vol-of-vol candidates) and [[infra-per-side-surfaces]] (skew/vanna
candidates). **Do not start before its inputs exist** — premature regression on a thin, friendly
sample is exactly the data-snooping §6 forbids.

## Done criteria
Residual time series persisted as-of, contract-typed; a documented, out-of-sample regression of the
residual against the named candidate factors; the dominant unmodeled exposure reported per
book/strategy and fed back into the model; `check-lookahead-bias` clean; no in-sample-only claims;
gate green.
