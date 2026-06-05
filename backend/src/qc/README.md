# qc — the validation plane

This package is the system's QC layer: a library of named checks that each take one
output from another workstream and return a stamped `contracts.QcResult` saying
whether it is healthy. The design rule is specificity. When a check fails it names
the exact maturity, quote, underlying, or solver that broke, in the result's context
payload — a generic "QC failed" red banner is the precise failure mode these checks
exist to prevent. Every check is a pure function; `run_id` and `run_ts` are passed
in, never read from a clock, so a check reproduces byte-for-byte in replay.

## Fastest path: the daily QC report

Load the platform config, build the threshold bundle, run the checks you have inputs
for, and roll them into a report:

    from qc import (
        thresholds_from_config, build_report, triage_table, escalation_level,
        check_collector_continuity,  # ... and the other nine
    )

    thresholds = thresholds_from_config(config.qc_threshold)
    results = [
        check_collector_continuity(summary, thresholds=thresholds, run_id=run_id, run_ts=run_ts),
        check_forward_stability(estimate, thresholds=thresholds, run_id=run_id, run_ts=run_ts),
        # ... one call per object you want validated
    ]
    report = build_report(results, run_id=run_id, run_ts=run_ts)
    table = triage_table(report)        # worst-first; each row names the offender
    level = escalation_level(report)    # "none" | "notice" | "page"

`report.overall_status` is the worst single status (`pass`/`warn`/`fail`).
`triage_table` drops the passing rows and orders what's left worst-first, with a
headline that names the failing object. `escalation_level` collapses the whole day
to one signal an alerting layer thresholds on.

## The ten checks

Each check reads one producing module's output, compares it to a threshold, and on
failure names the specific offending object. Thresholds come from
`QcThresholdConfig` plus the QC-owned supplements in `thresholds.py`; every result
carries `threshold_version`, the config section version. Of the config's three fields
only two are read here — `max_spread_pct` (quote health) and `min_chain_count` (chain
coverage); the third, `max_quote_age_seconds`, gates staleness upstream in C's snapshot
builder, not in any check below.

| check | reads | threshold key | severity on fail | names on fail |
|---|---|---|---|---|
| `check_collector_continuity` | `collectors.CollectorSummary` | `max_gap_count`, `min_coverage_ratio` | critical | `failing_session` (+ gap count, coverage) |
| `check_underlying_quote_health` | `snapshots.SnapshotBatch` | `max_spread_pct` (config) | critical | `failing_quote` (instrument_key) |
| `check_option_chain_coverage` | `snapshots.SnapshotBatch` + expected chain | `min_chain_count` (config) | warning | `underlying` + `missing_contracts` |
| `check_forward_stability` | `forwards.ForwardEstimate` | `max_residual_mad`, `min_forward_confidence` | warning | `underlying` + `failing_maturity` |
| `check_parity_residual` | `forwards.ParityLine` | `max_parity_residual` | warning | `underlying` + `failing_maturity` + worst index |
| `check_iv_solver_convergence` | `iv.IvResult` sequence | `max_non_convergence_ratio` | warning | `failing_solvers` (contract keys) |
| `check_surface_fit_error` | `surfaces.SliceFit` | `max_surface_rmse` | warning | `underlying` + `failing_maturity` |
| `check_calendar_sanity` | `surfaces.CalendarViolation` sequence | none — any violation fails | critical | `failing_maturity_short`/`_long` + `failing_k` |
| `check_greek_sanity` | `risk.PositionRisk` (+ optional `BrokerGreeks`) | `DEFAULT_RECON_TOLERANCE` (risk) | critical | `failing_contract` + offending `breaches` |
| `check_scenario_completeness` | produced vs expected scenario cells | none — any missing cell fails | critical | `missing_cells` (scenario_id, contract_key) |

Greek sanity also enforces a precondition that `risk.reconcile` does not: a supplied
broker row must be for the same contract as the line. A mismatch raises
`ContractKeyMismatchError` (naming both keys) rather than silently comparing the
wrong Greek — folding in the deferred item from ADR 0006.

## Anomaly detection

`detect_anomaly(observed, baseline, metric, target, ...)` flags a value that sits
too far from its rolling baseline. It uses a median/MAD robust z-score (so one old
spike in the baseline does not inflate the scale and hide a new one) and fails when
the score exceeds `anomaly_mad_multiplier` MADs. An empty baseline raises
`EmptyBaselineError` — "is this a spike" has no answer without a reference.

## Triage and escalation

The triage table is the operator's worst-first work queue: fails before warns, then
critical before warning before info, then larger measured magnitude first, with the
check name and target as a stable tie-break so the order is deterministic. Each row's
headline carries the named offender pulled from the context.

Escalation collapses a report to one of three levels. A critical-severity failure is
a `page`. Any other failure, or any warning, is a `notice`. A clean report is
`none`. That one rule is the single place the "alert on QC fails" policy lives, so it
cannot drift across call sites.

## What this package does not do

It does not read a clock, persist anything, or schedule itself — it is the pure check
library. The orchestration layer injects `run_id`/`run_ts`, runs the checks against
the day's stored outputs, and writes the `QcResult` rows to A's storage.
