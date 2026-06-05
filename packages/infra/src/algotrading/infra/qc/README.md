# infra.qc — the named static QC plane

A library of pure, named checks. Each takes a producing module's output (a snapshot
batch, a forward estimate, a slice fit, a risk line) plus a `QcThresholds` bundle and an
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

## The ten checks (+ anomaly)

`collector_continuity`, `underlying_quote_health`, `option_chain_coverage`,
`forward_stability`, `parity_residual`, `iv_solver_convergence`, `surface_fit_error`,
`calendar_sanity`, `greek_sanity`, `scenario_completeness` — plus `detect_anomaly`
(a median/MAD robust z-score against a rolling baseline). `greek_sanity` folds in ADR
0006's deferred reconcile precondition: a broker row for a different contract is a
mis-wired join and raises `ContractKeyMismatchError`, not a meaningless discrepancy.

## Entry points

- `thresholds_from_config(config.qc_threshold)` → `QcThresholds` (platform cut-offs +
  QC-owned supplements, each documented in `thresholds.py`).
- the ten `check_*` functions and `detect_anomaly`.
- `build_report(results, run_id=, run_ts=)` → `QcReport`; `escalation_level(report)` →
  `none` / `notice` / `page`.
- `named_offender` / `result_headline` — the offender-naming helpers the triage layer reuses.

## Inputs

Most checks consume a contract or producer type directly. `check_collector_continuity` is
the exception: the market-data plane's session summary is not a persisted contract and the
merged `collectors` package (C1) does not export one yet, so the check declares its minimal
input surface as the `CollectorContinuityInput` Protocol (`inputs.py`) — any object with
`session_id` / `gap_count` / `subscribed_count` / `covered_count` satisfies it, no adapter.

## What it does *not* do

It adds no analytics math (it judges M2/M3 outputs) and it owns no persisted shape. The
checks produce `QcResult` values; the collapse to the single persisted `triage_records`
shape (`contracts.TriageRecord`) is the sibling `infra.validation` plane's job. There is
deliberately no in-memory `TriageRow` reporting view — dropped in the merge (ADR 0010, C2);
derive any reporting view from `TriageRecord`.
