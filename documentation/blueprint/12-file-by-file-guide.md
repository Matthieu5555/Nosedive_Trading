> Source: blueprint PDF, pages 37–40. Faithful transcription — see ../blueprint/README.md for governance status.

# Part XII — File-by-file implementation guide

## `connectivity/session.py`

Responsibility. Own connection lifecycle to TWS or IB Gateway. Expose `connect()`, `disconnect()`, `is_healthy()`, and `heartbeat_loop()`. Maintain a small state machine: DISCONNECTED, CONNECTING, CONNECTED, DEGRADED, RECONNECTING. The module should not know anything about volatility mathematics. Its only job is to provide a reliable transport for broker interactions.

- Implement exponential backoff with jitter for reconnects.
- Emit structured logs on every state transition.
- Prevent duplicate client IDs by configuration validation at startup.
- Expose a heartbeat timestamp and age metric.
- Refuse to claim health until at least one successful broker round-trip has completed.

## `universe/contracts.py`

Responsibility. Resolve and normalize instruments. This file should provide the canonical dataclasses for underlyings and option contracts, plus serializer helpers. It should also define how instrument keys are constructed and parsed.

- Treat expiry, strike, right, exchange, currency, multiplier, and broker contract ID as separate fields.
- Add a method that round-trips from key string back into object form.
- Keep raw broker payloads in an adjacent storage layer for audit.
- Validate strike type conversion carefully to avoid integer/float drift.
- Add explicit tests for duplicate chain entries and missing multipliers.

## `collectors/raw_writer.py`

Responsibility. Persist normalized events quickly and safely. This module should not perform economics or analytics. It should batch writes where appropriate, flush on controlled intervals, and tag every write with collector session metadata.

- Use append-only semantics.
- Validate the schema before write, not after.
- Handle disk or object-store failures by surfacing a hard alert.
- Add per-partition write counters for operational reporting.
- Never silently drop malformed events; quarantine them with a reason code.

## `snapshots/builder.py`

Responsibility. Convert raw events into deterministic market-state snapshots. This module should define field precedence, quote-age limits, fallback logic, and the exact assembly of snapshot objects.

- Write pure functions for `latest_by_field`, `choose_reference_spot`, and `build_option_row`.
- Include state flags in the output object rather than relying on logs alone.
- Keep the builder deterministic by sorting events explicitly.
- Version the cadence and staleness thresholds via configuration.
- Provide a debug mode that prints the exact raw events chosen for one contract.

## `forwards/engine.py`

Responsibility. Build the forward curve and implied carry diagnostics. This file should contain candidate generation, weighting, outlier rejection, smoothing, and confidence-scoring logic.

- Separate candidate construction from candidate selection.
- Keep every intermediate candidate row for audit.
- Add a minimum candidate-count threshold before accepting a forward.
- Expose both weighted mean and median diagnostics.
- Include a fallback policy object rather than hard-coding the fallback.

## `iv/solver.py`

Responsibility. Price-to-vol inversion. The module should expose scalar and vectorized solve functions plus structured diagnostics objects. All convergence decisions should be explicit and tested.

- Use a bracketed solver as the primary safe path.
- Return diagnostics objects even on success.
- Document exactly which pricing model is assumed for each product family.
- Implement intrinsic and upper-bound checks before entering the root finder.
- Make the scalar path maximally readable and cover it with tests.

## `surfaces/calibration.py`

Responsibility. Fit surface slices and produce a regularized grid. The module should not fetch data itself. It should consume accepted IV points and emit parameters plus diagnostics.

- Persist raw points, accepted points, rejected points, fit parameters, and reconstructed grid values.
- Bound parameters and log bound hits.
- Compute RMSE and max error by slice.
- Support at least one fallback method for sparse slices.
- Add plotting helpers for operations and regression review.

## `pricing/european.py` and `pricing/american.py`

Responsibility. Canonical pricing functions and Greeks. These files form the trusted pricing library for the platform. All downstream analytics should call these files rather than reimplementing formulas elsewhere.

- Expose typed inputs and outputs.
- Keep unit conventions explicit in docstrings.
- Provide benchmark fixtures covering limiting cases.
- Separate pure pricing from vectorization wrappers.
- Test consistency between price and numerical derivatives.

## `risk/aggregation.py`

Responsibility. Merge positions with analytics results and produce line-level and aggregate sensitivities. The module should support grouping by any configured key such as underlying, maturity bucket, or desk category.

- Preserve line-level outputs for audit.
- Add currency handling if multiple currencies are present.
- Keep aggregate formulas transparent and queryable.
- Reconcile to broker Greeks only as diagnostics, never as the source of truth.
- Version the risk snapshot with analytics version and position source timestamp.

## `risk/scenarios.py`

Responsibility. Reprice portfolios under named scenarios. The module should support both full repricing and local approximations and expose contributor analysis.

- Define a typed Scenario object.
- Store every scenario with version and parameters.
- Allow scenario families to be filtered by report type.
- Separate scenario generation from scenario execution.
- Include top-contributor extraction in the core API.

## `qc/checks.py`

Responsibility. Implement validation checks as named reusable functions. Each check returns a measured value, a status, and supporting context.

- Keep thresholds in config, not in code.
- Assign stable reason codes to every fail path.
- Write summary and detail outputs separately.
- Track historical distributions of QC measures for trend analysis.
- Document operator meaning for each check in the module README.

## `orchestration/jobs.py`

Responsibility. Provide entry points for scheduled jobs. Jobs should be thin wrappers that orchestrate library calls, write manifests, and emit metrics.

- Use dependency ordering so downstream jobs do not run on incomplete upstream data.
- Write one manifest per job with parameters, versions, and outputs.
- Ensure reruns are idempotent or explicitly versioned.
- Surface clear exit codes for the scheduler.
- Add dry-run modes for operational safety.
