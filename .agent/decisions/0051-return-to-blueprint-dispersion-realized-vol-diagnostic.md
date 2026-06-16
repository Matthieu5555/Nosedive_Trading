# 0051 — Return to the blueprint: dispersion ρ̄ as a realized-vol diagnostic; retire constituent-option capture

- **Status:** accepted, 2026-06-15 (owner ruling, Vincent). **Supersedes [[0044-top-n-by-weight-dispersion-selector]] and [[0045-constituent-option-capture-merged-underlying-generic-basket]].**
- **Date:** 2026-06-15.
- **Implements:** the blueprint as plan-of-record ([[0011-blueprint-as-plan-of-record]]) — Part II
  §"Index or basket variance identity" (Eq. 23) and Part XV (raw is Tier-1, all analytics
  recomputed from raw). Realigns `TARGET.md` §0/§3-S1/§7.4, which had diverged.
- **Relates to:** [[0042-index-options-only-scope-ibkr-sole-broker]] (scope held — this *narrows*
  capture, never widens it), [[0035-index-registry-and-per-index-capture-schedule]] (the index +
  constituent **bars** backfill is unchanged), OQ-12.

## Context

A 2026-06-15 owner review traced a chain of problems back to a single divergence:

- **Capture was scoped by `constituent_top_n` (10).** `cp_rest_constituent_capture.py` captures the
  option chains of the top-10 constituents. Raw is irreplaceable (CP REST has **no historical
  option-quote endpoint** — `history_backfill.py:3-4`), so every name beyond today's selection
  loses option history **permanently**; a wider strategy can never be backfilled.
- **Throughput crisis.** The index alone takes ~7 min; the 11 underlyings capture **strictly
  serially** (`cp_rest_constituent_capture.py:442`), already at the edge of the close window. The
  full 50-name SX5E basket would be hours — it cannot fit (`ibkr-capture-cross-underlying-concurrency`).
- **Known ρ̄ bias.** Implied correlation was computed from constituent **implied** ATM vols, which
  only the captured top-10 have, "biasing ρ̄ high" (`universe.yaml:88`).

The root cause is that **ADR 0044/0045 baked a *strategy* choice (S1 trades single-name option
straddles) into the *immutable capture layer*.** The blueprint never asked for this:

1. **Eq. 23 needs *constituent volatilities*, not constituent *implied* vols.** The text
   (`02-math-framework.md:129`) requires "a vector of weights, **constituent volatilities**, and
   optional pairwise correlations **or a simplifying average-correlation assumption**", and calls
   the module a "**reusable risk and diagnostics primitive** — **not strategy logic**". Nowhere
   does it say "implied", and nowhere does it require capturing constituent option chains.
2. **The blueprint captures the index's options + the underlyings' prices.** Constituent
   volatility for the diagnostic is satisfiable by **realized** vol from the constituent **bars**
   we already backfill for the full membership (`history_backfill.py:110`).
3. **`top_n` / "top-N by weight" appears nowhere in the blueprint** — it traces only to TARGET
   §0/§3-S1/§7.4 via ADR 0044/0045.

## Decision

**Return to the blueprint. The dispersion / implied-correlation feature is a *diagnostic* computed
from data we already hold; it does not require capturing constituent option chains.**

1. **Capture scope = the blueprint's: the enabled index's option chains + the constituents'
   prices (daily bars).** No constituent option-chain capture. `constituent_top_n` is **removed**
   from the capture path; `cp_rest_constituent_capture.py` (the ADR-0045 lane) is **retired**. The
   index close-capture (`cp_rest_close_capture.py`) and the full-membership OHLC backfill
   (`history_backfill.py`) are unchanged.
2. **ρ̄ uses realized constituent volatility.** `signal_set` already computes
   `realized_vol_by_subject` for the index **and all constituents** from `daily_bar`
   (`signal_set.py:181,212-224`); `implied_correlation` is a pure function of
   `(weights, constituent_vols, index_vol)` (`correlation.py:47`) that is agnostic to the vol's
   origin. Rewire `σ_i` to the **realized** constituent vols (available for all 50 names, no top-N
   truncation) while `σ_I` stays the index's **implied** ATM vol — a hybrid implied/realized ρ̄
   sanctioned by Eq. 23. This **removes the top-10 bias** at its source.
3. **`dispersion_top_n` (ADR 0044) survives as a pure *strategy-side* selector** over the banked
   raw — it never gates capture again. If a future strategy genuinely needs to *trade* single-name
   options (pure implied-correlation dispersion), that is a new, separately-ruled decision with its
   own capture-cost / entitlement / throughput case — not the default.

### The realized-vol caveat (stated honestly)

Pure *implied* correlation carries the volatility risk premium (forward-looking, what the market
prices); a hybrid implied-index / realized-constituent ρ̄ has a level basis and is a **proxy**. For
a **diagnostic / signal** — which is exactly what the blueprint specifies and what this decision
adopts — the proxy is appropriate and its time-series (rich/cheap) stays usable. Trading a pure
implied-correlation dispersion would re-open constituent-option capture; that is deferred, not
assumed.

## Consequences

- **The throughput "emergency" and the permanent-history-loss risk both dissolve** — they were
  artifacts of constituent-option capture, which no longer happens. `ibkr-capture-cross-underlying-
  concurrency`, `ibkr-snapshot-warmup-concurrency`, `ibkr-intraday-conid-cache`, and
  `EMERGENCY-capture-throughput`/`-constituent-lane-activation` become **moot for the option lane**
  (any remaining value is only for the index's own chain, already fast enough). OQ-12 is resolved
  by this ADR.
- **Implementation is DEFERRED until after the 2026-06-15 evening close run** (owner: do not change
  the live capture path before tonight's run). Tonight runs unchanged (top-10), one last time.
- **Goldens / config hash:** removing `constituent_top_n` from the capture path moves the `universe`
  hash by design; regenerate when implemented.
- **No code, config, or systemd-timer change is made by this ADR** — it records the decision only.
- **Scope (ADR 0042) is held and reinforced:** index-options-only; this strictly *reduces* what is
  captured. The schedule (22:45 Europe/Berlin, Eurex close 22:00 + margin) is unrelated and
  unchanged — it was an operational choice, never a blueprint value.
