# infra.observability

Run lineage: a durable, auditable record of what the orchestration plane *did*. Where
`orchestration.run_state` is the EOD stage ledger that drives restart/backlog logic,
this package records the *lineage* of each job run — environment, code version, config
hashes, input/output partitions, correlation id, status — into the M10
`RunRegistry` (ADR 0015 serving tier).

## Fast path

```python
from algotrading.infra.observability import run_job

result = run_job(
    "reconstruct", lambda: reconstruct_day(...),
    registry=registry, environment="prod", code_version=ver,
    config_hashes={"platform": cfg_hash},
    run_id="2026-05-29-reconstruct",      # explicit id → restart overwrites, never duplicates
    correlation_id=session_id,            # links the collector session to the analytics it fed
)
result.value   # whatever the job returned
result.record  # the persisted RunRecord (manifest.status is OK or FAILED)
```

A failing job is recorded as `FAILED` and then **re-raised** — the failure is
observable, never swallowed. A re-run under the same `run_id` overwrites its record, so
a restart is idempotent. `clock` is injected (defaults to wall-clock `now`) so a
deterministic caller supplies its own time.

## Scope

This is the engine-side observability piece. The operator-facing **metrics, alerts, and
dashboard** live next door in `algotrading.infra.orchestration` (they read recorded
state and the live metric registry). Alert **routing** (escalation → channel) and live
HTTP **health** endpoints are part of the serving/API tier and are handled there, not
here — the run-lineage record is what the engine needs and what lands now. See ADR 0026
for the reconciliation of which orchestration/observability helpers were adopted.

## Tests

`packages/infra/tests/test_observability_runner.py` — OK/FAILED recording, re-raise on
failure, run-id idempotency, correlation-id threading, generated-id fallback.
