# 0010 — QC + validation merge (M6): keep our checks, adopt Vincent's depth, one triage record

- **Status:** accepted
- **Date:** 2026-06-05

## Context

This is workstream **M6** of the merge with Vincent's AlgoTrading (see
`tasks/M6-qc-validation.md`, `tasks/TASKBOARD.md`). Both repos independently built a
quality layer. Ours (`src/qc`) is a library of **ten named run-level checks**, each
returning a stamped `contracts.QcResult` that names the specific failing object
(maturity / quote / solver), plus a report/triage/escalation roll-up. Vincent's has a
distinct, separate **`validation` layer** we lack: rolling-baseline anomaly detection,
a triage table, escalation thresholds, and a Parquet triage store.

The M6 spec says to keep our ten checks, fold in any checks of his we don't have, adopt
his anomaly/triage depth as a *sibling* to QC, and — critically — collapse the **two
result shapes into one** before M7 builds reporting on them.

Per the user, M6 is built **in the current flat `backend/src` layout**, not the
`packages/` monorepo the spec's paths assume (M0 had not landed; a separate agent is
doing that restructure in place and will relocate `src/qc` + `src/validation` under
`packages/infra/...` later). Only the physical location is deferred; the merge itself is
complete here.

## Decision

1. **Keep our QC plane as the named-check library; nothing material to fold in.** On
   inspection, our ten run-level checks already subsume Vincent's six run-level
   validation checks (his consume a `DayReconstruction` aggregate we don't have — we use
   the Nautilus actor spine, ADR 0007), and our `snapshots/quote_quality.py` already
   covers six of his eight quote-level checks. The one genuinely-missing check,
   **`mark_iv_divergence`** (platform IV vs broker mark-IV), needs a broker-IV field our
   snapshot contract lacks, so it belongs in **M2 (snapshots)**, not M6 — recorded here
   rather than forced into this plane against a field that doesn't exist.

2. **Adopt Vincent's depth as a new sibling plane, `src/validation`.** This is the part
   we genuinely lacked: a richer rolling-baseline anomaly detector with an explicit
   **`no_baseline`** state (cold-start runs are reported, never assumed normal),
   **warn/fail bands** and a **`min_baseline`** floor, and a sweep over many metrics at
   once. It is pure (no I/O, no clock), the same discipline as `src/qc`.

3. **Collapse the two result shapes into one persisted `TriageRecord`.** Rather than
   keep our `TriageRow` and Vincent's `TriageRecord` as two shapes a reporting layer
   must reconcile, both planes now fold into a single `contracts.TriageRecord`
   (`source="qc"` | `"validation"`), built by `validation.build_triage`, ordered
   worst-first by one rule, escalated by one policy (`validation.escalation_level`). The
   QC side reuses the *same* `qc.named_offender` / `qc.result_headline` an operator
   already reads, so the specificity discipline survives the merge — "surface_fit_error
   fail [failing_maturity=0.5]", never "QC red".

4. **Persist through our storage port, not a bespoke store.** Vincent's standalone
   Parquet `TriageStore` is exactly the kind of per-type store our architecture routes
   through the contract registry + `ParquetStore` instead (as `QcResult` already is). So
   `TriageRecord` is registered as the **`triage_records`** table (additive, non-breaking
   change to `contracts`), persisted via `store.write("triage_records", records)`. The
   merge thus *removes* a parallel persistence mechanism rather than importing it.

5. **The validation plane stays pure; orchestration operates it.** Just as `src/qc` is
   pure and `orchestration/qc_job.py` injects `run_id`/`run_ts`, runs the checks, and
   writes the rows, the metric assembly + baseline read + triage-table write for
   validation are the orchestration layer's job (M7), not this plane's.

## Consequences

- `src/validation` is new (`anomaly`, `state`, `engine`, `triage` + README). `src/qc`
  gains two public helpers (`named_offender`, `result_headline`) so triage reuses its
  offender-naming logic instead of duplicating the priority list; its public surface is
  otherwise unchanged, so `orchestration/qc_job.py` and `alerts.py` are untouched.
- `contracts` gains the `TriageRecord` dataclass + a `triage_records` `TableSpec`
  (additive). The robust median/MAD z-score is implemented twice (once per plane,
  independently tested) for layer independence; a future shared `utils.robust` could
  unify them — noted, not done.
- Tests: anomaly bands/`no_baseline`/determinism with a hand-computed z-score oracle,
  the validation engine, and the unified triage (cross-plane ordering, one escalation
  policy, storage round-trip, and malformed-record rejection at the seam). Full gate
  (ruff/mypy/pytest) green.
- **Open for M7:** a `validation_job` mirroring `qc_job` (assemble metrics + baselines,
  run validation, write `triage_records`). **Open for M0:** relocate `src/qc` +
  `src/validation` + the `triage_records` contract under `packages/infra/...`.

## C2 update (relocation to `packages/infra`) — 2026-06-05

M6 landed flat in `backend/src`; **C2** (`tasks/C2-qc-validation.md`) ports it under
`packages/infra/src/algotrading/infra/{qc,validation}` on the M0 frozen seam. The merge
decisions above hold; three refinements are recorded here so the next agent does not
reverse-engineer them:

1. **Three sources, not two.** The `TriageRecord.source` discriminant is now
   `"qc" | "validation" | "anomaly"`, matching the data dictionary on the contract. The
   anomaly/validation split is read off the check's `reason_code` in exactly one place
   (`validation.triage._validation_source`): a `REASON_METRIC_ANOMALY` flag is
   `source="anomaly"` (the rolling-baseline plane proper), any other validation check is
   `source="validation"`. One rule, so the discriminant cannot drift from the reason a
   check declared. (M6's flat code emitted only `qc`/`validation`.)

2. **`TriageRow` is dropped, not ported.** The legacy in-memory reporting view (our old
   `qc.report.TriageRow`/`triage_table`) is gone — it must not become a second shape in
   `packages`. The single persisted shape is `TriageRecord`; derive any reporting view
   from it. (Per C2's explicit mandate.) `qc.report` keeps `QcReport`, `build_report`,
   `named_offender`, `result_headline`, and a QC-plane `escalation_level(QcReport)`; the
   unified `escalation_level(records)` over `TriageRecord` stays in `validation.triage`.

3. **`check_collector_continuity` consumes a Protocol, not a collectors type.** The
   merged `collectors` package (C1) does not export a session-summary type yet, and a
   summary is not a persisted contract. Rather than reach into a shape C1 has not frozen
   or invent a parallel record, the check declares its minimal input surface as the
   structural `qc.CollectorContinuityInput` Protocol (`qc/inputs.py`) — any object with
   `session_id` / `gap_count` / `subscribed_count` / `covered_count` satisfies it, so
   C1's eventual `CollectorSummary` slots in with no adapter.

Gate in isolation: ruff + mypy (12 source files) + import-linter (2/2 kept) clean; the
five C2 test files pass (97 tests). The robust z-score remains implemented once per plane
(`qc.robust_z_score`, `validation.robust_zscore_vs_baseline`), each independently
oracle-tested — the shared-`utils` unification is still noted, not done. **Open for C3:**
the `qc_job` / `validation_job` that inject `run_id`/`run_ts`, run the planes, and persist
`triage_records` through `StorageRepository`. **Open for C5:** retire `backend/{qc,validation}`.
