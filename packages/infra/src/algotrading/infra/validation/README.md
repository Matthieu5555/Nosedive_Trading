# infra.validation — the rolling-baseline plane + the unified triage collapse

The sibling of `infra.qc`. QC asks static, per-object questions ("did this fit pass its
RMSE cut-off"). Validation asks a different one: **is this whole run trustworthy relative
to its own recent history?** A run can pass every static check and still be anomalous —
a usable-quote count that quietly collapses, a fit error creeping up day over day — and
that is exactly the failure this plane catches, with a robust (median/MAD) z-score so a
few baseline outliers cannot inflate the scale and mask a real shift. Too little history
is `NO_BASELINE`, never silently "normal".

## Entry points

- `run_validation(run_id=, underlying=, as_of=, current_metrics=, baselines=, thresholds=)`
  → `ValidationOutcome` (a rolled-up `ValidationReport` + the raw `AnomalyOutcome`s).
- `build_triage(qc_report=, validation=)` → the unified worst-first `tuple[TriageRecord, …]`.
- `escalation_level(records)` → `none` / `notice` / `page`.

`run_id` / `as_of` are injected, never read from a clock, so a pass reproduces in replay.

## One persisted shape, three sources

Both planes collapse into the **single** `triage_records` table (`contracts.TriageRecord`)
so there is one thing to persist, order, and escalate on. `source` discriminates the row's
origin:

- `qc` — a named static QC check (`triage_from_qc`).
- `anomaly` — a rolling-baseline metric flag (`reason_code == REASON_METRIC_ANOMALY`).
- `validation` — any other validation check (structural/cross-field, not an anomaly score).

The anomaly/validation split is read off the check's `reason_code` in one place
(`_validation_source`), so the discriminant cannot drift from the reason a check declared.
There is no second persisted shape: the legacy in-memory `TriageRow` was dropped in the
merge (ADR 0010, C2); derive any reporting view from `TriageRecord`.

## Discipline preserved across the merge

Specificity survives the collapse — a QC row's headline and offender name are built by the
*same* `qc.result_headline` / `qc.named_offender` an operator already reads, so a specific
failure does not decay into "QC red". Escalation is one policy: a critical-severity failure
pages, any other failure or warning is a notice, a clean run escalates to nothing.

These records are pure values; persisting them (to `triage_records` through
`StorageRepository`) is the orchestration layer's job (C3), not this plane's.
