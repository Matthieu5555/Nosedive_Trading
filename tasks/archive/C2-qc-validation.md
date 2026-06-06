# C2 — Port QC + validation/triage into `packages/infra`

- **Owns:** `packages/infra/src/algotrading/infra/{qc,validation}/**` (+ READMEs).
- **Depends on:** M0 — `contracts.TriageRecord` and the `triage_records` `TableSpec` are already registered in `packages/infra/contracts`.
- **Blocks:** C3 (orchestration's `qc_job`/`validation_job`; the handover e2e runs QC).
- **State going in:** the contract collapsed correctly — `triage_records` is the single persisted shape in `packages/infra/contracts`. But the **logic never moved**: the ten named checks and the anomaly/triage producers live only in `backend/src/{qc,validation}`; the `packages/infra/{qc,validation}` skeletons are empty `__init__.py` stubs.

## Objective

Move the QC library and its sibling anomaly/triage validation plane into `packages/infra`, both feeding the **one** `triage_records` table (ADR 0010). One persisted shape, three sources (`qc` / `validation` / `anomaly`).

## What to do

1. **Port the ten named checks** from `backend/src/qc/{checks,report,result,thresholds,errors}.py` into `packages/infra/.../qc`, under the `algotrading.infra.*` namespace. Keep the named-offender payloads (`named_offender`, `result_headline`) — a check names the object that failed, it does not return a bare bool.
2. **Port the anomaly/triage plane** from `backend/src/validation/{anomaly,engine,triage,state}.py` into `packages/infra/.../validation`, including `build_triage` / `escalation_level`.
3. **Collapse to one shape.** Both planes emit `contracts.TriageRecord` rows persisted via `StorageRepository` into `triage_records`. **Drop the legacy `TriageRow`** second shape (`backend/src/qc/report.py`) — it is a harmless in-memory reporting view in the flat tree, but it must **not** be carried into `packages` as a parallel persisted shape. If a reporting view is still wanted, derive it from `TriageRecord`.

## Frozen seam

Produces `TriageRecord` rows only; persists through `StorageRepository`; consumes M2/M3 analytics outputs (snapshots/forwards/surfaces/risk) as read-only inputs. Adds no storage table — `triage_records` already exists.

## Test surface (in `packages/infra/tests`)

Read `tasks/TESTING.md` first.
- **Each of the ten checks** with an **independently-derived** failing object (hand-built input that violates exactly one threshold), asserting the named offender in the payload — not just that it failed.
- **The collapse:** a `qc` result, a `validation` result, and an `anomaly` result all land in `triage_records` with the same column shape and the correct `source` discriminant.
- **Persistence round-trip** of `TriageRecord` through `ParquetStore`.

## Done criteria

`qc` and `validation` live in `packages/infra`, both feeding one `triage_records` table; no `TriageRow` parallel shape; tests green in the root gate. `backend/{qc,validation}` are now stale dupes — hand them to C5.

## Gotchas

One table, three sources — resist re-introducing a second persisted shape. The checks compute over M2/M3 outputs; they add no analytics math of their own.
