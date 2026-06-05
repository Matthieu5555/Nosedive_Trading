# M6 — QC, validation and triage

- **Branch:** `feat/merge-qc`
- **Owns:** `packages/infra/src/algotrading/infra/{qc,validation}/**` (+ READMEs).
- **Depends on:** M0 (contracts). Pure over contracts — can start early.
- **Blocks:** M7 (the QC/validation jobs it schedules).

## Objective

Merge the two quality layers. Both repos have a QC check library; Vincent adds a distinct `validation`/anomaly/triage layer. Keep our specific-failing-object discipline, adopt his anomaly/triage depth, land one named-check library plus one validation layer.

## What to merge

- **QC bake-off:** ours (`backend/src/qc/{checks,engine,result,thresholds,report}.py`) vs Vincent's (`infra/qc/{checks,engine,state}.py`). Keep our **ten named checks** with the rule that each names the specific failing object — collector continuity, underlying quote health, chain coverage, forward stability, parity residual, IV convergence, surface fit error, calendar sanity, Greek sanity, scenario completeness — each returning status, severity, measured value, threshold version, and a context payload that names the failing **maturity/quote/solver**, not a generic red banner. Fold in any checks Vincent has that we don't.
- **Adopt from Vincent:** the `validation` layer — `infra/validation/{anomaly,engine,triage,config,state}.py`: anomaly detection against rolling baselines, the triage table, escalation thresholds, config-driven. This is depth we don't have; bring it in as a sibling to QC, backed by M1's `triage_store`.
- One `QcResult`/validation-result contract, agreed with M7.

## Frozen seam

QC and validation are pure over M0's contracts and M2/M3's outputs. They produce result objects M7 persists (via M1's `triage_store`) and reports on. No I/O in the check functions themselves.

## Test surface

Read [TESTING.md] first. Specific to M6:
- Each named check: a passing fixture and a failing fixture, and on failure a `QcResult` whose context payload **names the specific** failing maturity/quote/solver (assert the specificity — a generic banner is the failure mode this exists to prevent).
- Anomaly detection flags an injected spike against a rolling baseline; does not flag a normal series.
- Triage escalation: a check crossing its threshold lands in the triage table at the right severity.
- Adopt Vincent's `tests/{qc,validation}/**` fixtures.

## Done criteria

One named-check QC library (specificity preserved) plus Vincent's anomaly/triage validation layer, both pure, results persisted through M1, gate green with passing+failing fixtures per check and the anomaly/triage tests.

## Gotchas

Don't lose the specificity rule in the merge — "surface fit failed for AMS 2026-09 at vega node 3" beats "QC red". Keep checks pure; the scheduling/persistence is M7's. Two result shapes (ours + his) must collapse to one before M7 builds reporting on them.
