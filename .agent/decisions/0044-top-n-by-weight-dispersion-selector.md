# 0044 — Top-N-by-weight dispersion selector over the as-of basket

- **Status:** ⚠️ **PARTIALLY SUPERSEDED by [[0051-return-to-blueprint-dispersion-realized-vol-diagnostic]]
  (2026-06-15).** The `top_n_by_weight` selector and `dispersion_top_n` config **survive as a pure
  strategy-side selector over banked raw**; what 0051 removes is their use as a *capture* gate
  (`constituent_top_n`) — capture returns to the blueprint scope (index options + constituent
  prices). ~~accepted, 2026-06-14 (S1 precondition implemented under
  `infra-sx5e-weighted-membership`).~~
- **Date:** 2026-06-14.
- **Implements:** TARGET §0 (universe = one enabled index + its top-N by weight, point-in-time)
  and §3 S1 (the dispersion book trades the heaviest constituents). Unblocks
  `ibkr-constituent-option-capture` and the S1 dispersion strategy.
- **Relates to:** [[0028-configuration-and-reproducibility-standard]] (the new `dispersion_top_n`
  is hashed economic config, not a `.py` literal), [[0035-index-registry-and-per-index-capture-schedule]]
  (the index whose basket is ranked), [[0033-asof-join-membership]] / WS 1A (the `members`
  resolver this builds on), [[0023-nautilus-runtime-spine-and-library-leverage]] (lean on the
  one as-of resolver, do not re-implement it). Scope guardrail: index-options-only, IBKR sole
  broker, SX5E sole live index (ADR 0042).

## Context

S1 dispersion is "trade the top-N constituents of an index by index weight, point-in-time".
The 2026-06-14 IBKR-coverage audit found that, while the membership store, the bitemporal
`IndexConstituent` contract, and the look-ahead-gated as-of resolver (`members`) all existed,
**nothing ranked a basket by weight** — there was no `top_n_by_weight`. The weighted SX5E source
itself was already present: `CsvFileSource` reads the committed SSGA SPDR-ETF (FEZ) holdings
snapshot `configs/index_weights/sx5e_ssga_fez_2026-06-09.csv` (50 names, weights in **percent**),
ingested through the existing `ingest_membership_changes`. So the real gap was the selector, not
the source.

## Decisions

1. **The selector is a thin ranking on top of `members`, not a second resolver.**
   `top_n_by_weight(store, index, as_of_date, n, *, known_as_of=None)` calls `members` (the one
   look-ahead-gated as-of join, ADR 0033) and then sorts. It adds *only* the rank, so the
   look-ahead audit keeps policing a single surface. The rank is therefore always taken on the
   basket as it stood on `as_of_date` — never on the latest membership.

2. **Deterministic order: descending weight, ties broken by ascending constituent symbol.**
   Equal-weight names must resolve reproducibly across storage/ingest order; a single sort key
   `(-weight, constituent)` over Python's stable sort gives the full deterministic order in one
   pass.

3. **A basket with any labeled-unavailable (`None`) weight is refused, labeled.** You cannot rank
   what isn't known. Dropping the unweighted names would bias the selection toward names that
   happen to carry a weight; zeroing them would rank them last on a fiction. Both are the
   economic-correctness bug the membership layer refuses everywhere, so this raises
   `MembershipRankingError` (a new `UniverseError` subclass) naming the offending names. A
   non-positive `n` is refused the same way. An **empty** basket (unknown index / pre-history
   date) is *not* an error — there is nothing to rank, so it returns `()`.

4. **`n` smaller than the basket returns the top of what exists; `n` larger returns all of it.**
   A smaller live index is a legitimate state, never padded and never an error.

5. **Weights are ranked as raw magnitudes — normalization is not required.** The shipped SSGA
   feed is in percent and sums to ≈ 96, not 1.0 (an ETF's holdings exclude cash/fees and the
   feed is partial), so it does *not* go through the `complete_snapshot` weights-sum-to-1.0 guard;
   it is ingested as an incremental change set that preserves the raw percent values. Ranking
   needs only the relative order, so a percent source ranks identically to a fractional one. The
   sum-to-1.0 guard remains available for a future fully-weighted vendor (Siblis, OQ-3).

6. **`n` lives in config, not a `.py` literal.** `UniverseConfig.dispersion_top_n` (default 10 =
   the course's top-10; set to 50 in `configs/universe.yaml` = the theory's top-50, the SX5E's
   size) is economic — it decides which names trade and so which constituent chains are captured,
   changing which records exist — so it folds into `config_hashes["universe"]` (ADR 0028 / C7).
   The selector itself takes `n` as an injected parameter; the consumer
   (`ibkr-constituent-option-capture`, the S1 strategy) sources it from this field.

## Consequences

- The `universe` bundle hash (and the folded whole-config hash) moved BY DESIGN with the new
  field; `qc`/`pricing`/`scenarios` stay byte-identical (section isolation). This is a pre-capture
  dev change — no banked historical record carries the old hash. The pinned-oracle test in
  `test_config_core.py` is regenerated with a dated note, exactly as the `discovery_working_vol`
  and QC-residual-units changes were.
- `ibkr-constituent-option-capture` consumes `top_n_by_weight(store, "SX5E", as_of, n)` with
  `n = config.universe.dispersion_top_n`; the resolved constituent symbols are the names to
  qualify and capture chains for.
- A future fully-dated weighted vendor (Siblis) lands on the same `MembershipChange` contract and
  the same selector — no change here.
