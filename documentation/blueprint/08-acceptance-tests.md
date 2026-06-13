> Source: blueprint PDF, pages 34–35. Faithful transcription — see ../blueprint/README.md for governance status.

# Part VIII — Acceptance tests by module

## Connectivity and access

1. A fresh environment can establish a session and retrieve at least one quote.
2. Heartbeats are observable and reconnects are logged with timestamps.
3. A wrong credential or entitlement failure produces a clear, structured error.

## Universe service

1. The active option chain can be reproduced for a fixed as-of date.
2. Duplicate contract records are removed deterministically.
3. Multiplier, currency, expiry, and exchange are populated for every active contract.

## Collector service

1. Raw events are written continuously during market hours.
2. Process restart does not corrupt or duplicate records beyond documented idempotency behavior.
3. Session summary contains event counts and reconnect counts.

## Forward engine

1. Weighted forward estimate is stable under small strike-set perturbations.
2. Outlier candidates are rejected for the documented reason.
3. Diagnostics explain every poor-quality maturity.

## IV solver

1. Reference contracts converge to expected IVs.
2. Out-of-bounds prices return structured solver failures.
3. Residual and iteration count are stored per solve.

## Surface engine

1. Accepted points and fitted values are queryable together.
2. Surface fit parameters are reproducible given the same inputs.
3. Calendar monotonicity diagnostic is computed for all monitored maturities.

## Risk and scenarios

1. Portfolio aggregates reconcile to line-level sums.
2. Scenario outputs are reproducible from snapshot, positions, and scenario version.
3. Top contributors can be identified for every worst-case scenario.
