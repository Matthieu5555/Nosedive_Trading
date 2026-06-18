# 0059 — Re-enable full-membership constituent option capture (max option data); keep the ρ̄ realized-vol diagnostic

- **Status:** accepted, 2026-06-18 (owner directive, Matthieu). **Amends [[0051-return-to-blueprint-dispersion-realized-vol-diagnostic]]** on its *capture-scope* clause only.
- **Date:** 2026-06-18.
- **Relates to:** [[0042-index-options-only-scope-ibkr-sole-broker]] (the index-options-only default is *extended*, not broken — constituent options are now captured too, both on EUREX), [[0044-top-n-by-weight-dispersion-selector]] (the strategy-side `dispersion_top_n` is untouched), [[0045-constituent-option-capture-merged-underlying-generic-basket]] (the lane this resurrects, now scoped to full membership rather than top-N).

## Context

ADR 0051 retired the constituent-option-capture lane (`cp_rest_constituent_capture.py`) and re-based
the dispersion ρ̄ diagnostic on **realized** constituent vol from the daily bars we already backfill.
Two of its three motivating problems were artifacts of a *narrow, point-in-time top-N* capture:

- **Permanent option-history loss** beyond today's top-N selection (CP REST has no historical
  option-quote endpoint).
- **Throughput** of a serial cross-underlying march at the edge of the close window.

The owner's directive on 2026-06-18 is **maximum option-surface data: capture index options AND
constituent options at every EOD close, over the full membership** (all ~40-50 SX5E names with a
weight), not a top-N slice. Two facts make this safe to do now where ADR 0051 judged it unsafe:

1. **Live entitlement is verified.** A live probe confirmed the account resolves full EUREX option
   chains for the top constituents (the entitlement pre-flight ADR 0051 also deleted is *not*
   resurrected — it is unnecessary).
2. **Full membership removes the permanent-loss objection at its root.** The loss was "every name
   *beyond today's selection*". Capturing the *whole* membership every close means there is no
   excluded tail to lose; a wider strategy can be backfilled from what was banked.

The throughput concern is addressed by the lane's existing one-shared-budget concurrency
(`capture_pool_size`, the bounded cross-underlying + within-walk semaphore) that ADR 0045 built and
this ADR re-instates; it is a pacing knob, byte-identical to the serial result.

## Decision

**Re-enable the constituent-option-capture lane, scoped to FULL MEMBERSHIP, additive to (never a
replacement for) the realized-vol ρ̄ diagnostic.**

1. **Capture scope = index options + all constituent options.** `cp_rest_constituent_capture.py`
   (`collect_index_and_constituents_basket`) is resurrected from `e9831e9^` and adapted to current
   collector APIs. `live_basket_source` / `gateway_basket_source` widen to the index + every
   constituent when a store is present and the capture flag is on; the production gateway path is
   included.
2. **Full membership, config-gated.** Two new `universe` knobs (economic, hashed — never `.py`
   literals, ADR 0028): `capture_constituents: bool` (default **true**; false = the ADR-0051
   index-only behavior) and `constituent_top_n: int | null` (default **null = the full as-of
   membership**, mirroring how ρ̄ reads the whole basket; an int caps it to the top-N for a narrower
   fire). Members are resolved point-in-time from banked 1A membership weights via
   `top_n_by_weight` over the full `members(...)` set; a missing-weight or empty basket fails loud
   (`ConstituentLaneError` / `MembershipRankingError`), never a silent index-only fall-through.
3. **ρ̄ from realized vol is KEPT exactly as ADR 0051 set it.** The dispersion signal math,
   `signals.basket_size: null` (full-membership realized-vol ρ̄), and the implied-index / realized-
   constituent hybrid are **unchanged**. Constituent option captures are an *additional* data
   surface (banked raw + the `constituent_capture_outcomes` ledger), not an input to ρ̄. ADR 0051's
   realized-vol-diagnostic decision stands on its own merits and is not reopened.

## Consequences

- **Contracts:** `ConstituentCaptureOutcome` + the `constituent_capture_outcomes` QC table are
  restored. The table partitions by `(trade_date-of-run_ts, underlying)` via the standard
  partitioner (no bespoke run-partition flag, which the storage layer has since dropped).
- **Config hash:** adding `capture_constituents` / `constituent_top_n` / `capture_pool_size` to the
  `universe` bundle moves the `universe` hash and the whole-config `config_hash` by design; the
  golden oracle in `test_config_core.py` is regenerated. `qc`/`pricing`/`scenarios`/`rates` are
  unchanged.
- **Frontend:** out of scope and untouched (owner-owned). The ADR-0051 CoverageTable constituent
  column is *not* re-added by this pass.
- **Entitlement probe stays retired** (ADR 0051) — live entitlement is verified, so the pre-flight
  is dead weight.
- **Throughput** is bounded by `capture_pool_size`; the full 50-name fire is a known cost the owner
  has accepted in exchange for the option-surface data.
