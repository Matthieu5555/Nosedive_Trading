# infra.qc — the named static QC plane

A library of pure, named checks. Each takes a producing module's output (a snapshot
batch, a forward estimate, a slice fit, a risk line) plus the typed, hashed
`core.config.QcThresholdConfig` (every economic cut-off lives there, ADR 0028) and an
injected `run_id` / `run_ts`, and returns one stamped `contracts.QcResult`. No clock is
read inside a check, so a check is a pure function of its inputs and reproduces
byte-for-byte in replay.

## The one rule: specificity

A QC failure must name the **exact** offending object — this maturity, this quote, this
solver — never a generic red banner, which is the precise failure mode the plane exists
to prevent. Every failing result carries the offender under an explicit key in its
`context` (a canonical-JSON blob). `named_offender` / `result_headline` read that name
back out; the unified triage layer reuses them so the same offender is named the same way
everywhere.

## The twelve checks (+ anomaly)

The ten instrument-agnostic checks — `collector_continuity`, `underlying_quote_health`,
`option_chain_coverage`, `forward_stability`, `parity_residual`, `iv_solver_convergence`,
`surface_fit_error`, `calendar_sanity`, `greek_sanity`, `scenario_completeness` — plus two
grid-aware checks (WS 1H) that validate WS 1F's projected (tenor × delta-band) grid *as a
grid*, not as a flat chain:

- `tenor_coverage_floor` — for each pinned tenor (P0.1 grid, config) the count of usable
  grid points clears that tenor's configured floor (`>=` passes; a tenor absent entirely is
  a breach, not a skip). Names the specific breaching tenors with measured-vs-floor counts.
- `delta_band_completeness` — for each pinned tenor the selected strikes' deltas span the
  configured Δ-band (30Δ put → ATM → 30Δ call) with no interior gap wider than the
  configured max step. The band edges come from **config**, never from the data under test,
  so a thin chain fails rather than silently defining its own band. Empty, single-strike,
  and one-sided tenors are explicit labelled breaches.

Both grid checks are critical-severity (a grid breach pages) and key on config, not on the
data — their cut-offs live in the typed `qc.grid` block (ADR 0028), not as `.py` literals.

Plus `detect_anomaly` (a median/MAD robust z-score against a rolling baseline).
`greek_sanity` folds in ADR 0006's deferred reconcile precondition: a broker row for a
different contract is a mis-wired join and raises `ContractKeyMismatchError`, not a
meaningless discrepancy.

## Entry points

- the checks read `config.qc_threshold` (`QcThresholdConfig`) directly (M37): the three
  cross-cutting scalars at the top level, every other cut-off on the nested block that
  owns it (`.continuity.*`, `.forward_engine.*`, `.fit_tolerance.*`,
  `.anomaly.mad_multiplier`, `.grid.*` incl. `.grid.floor_for(tenor)`); the version stamp
  on every result is `.version`. `thresholds.py` keeps `QcThresholds` /
  `thresholds_from_config` only as transitional aliases for the orchestration call sites.
- the twelve `check_*` functions and `detect_anomaly`.
- `build_report(results, run_id=, run_ts=)` → `QcReport`; `escalation_level(report)` →
  `none` / `notice` / `page`.
- `named_offender` / `result_headline` — the offender-naming helpers the triage layer reuses.

## Inputs

Most checks consume a contract or producer type directly. `check_collector_continuity` is
the exception: the market-data plane's session summary is not a persisted contract and the
merged `collectors` package (C1) does not export one yet, so the check declares its minimal
input surface as the `CollectorContinuityInput` Protocol (`inputs.py`) — any object with
`session_id` / `gap_count` / `subscribed_count` / `covered_count` satisfies it, no adapter.
The two grid checks take the same approach: they read WS 1F's projected grid cells through
the `GridPointInput` Protocol (`underlying` / `tenor_label` / `delta`), so the QC plane does
not import 1F's concrete `ProjectedOptionAnalytics` (which satisfies it with no adapter).

## What it does *not* do

It adds no analytics math (it judges M2/M3 outputs) and it owns no persisted shape. The
checks produce `QcResult` values; the collapse to the single persisted `triage_records`
shape (`contracts.TriageRecord`) is the sibling `infra.validation` plane's job. There is
deliberately no in-memory `TriageRow` reporting view — dropped in the merge (ADR 0010, C2);
derive any reporting view from `TriageRecord`.
