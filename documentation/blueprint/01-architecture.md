> Source: blueprint PDF, pages 4–5. Faithful transcription — see ../blueprint/README.md for governance status.

# Part I — System architecture and engineering principles

## Architecture principles

### Determinism

Given the same raw observations, configuration version, and code version, the system must reproduce identical derived analytics. Reproducibility is a non-negotiable property because all later QA depends on it.

### Layer separation

Split the stack into five layers: connectivity, raw capture, normalized market state, derived analytics, and portfolio/risk analytics. No downstream layer may silently overwrite an upstream observation.

### Idempotent processing

All batch jobs must be restartable. If a job is re-run for a given date partition, the outcome must either be byte-for-byte identical or intentionally versioned as a new analytics release.

### Transparency

Every computed object must carry provenance: source timestamps, calculation timestamp, code version, config hash, and the source records used to compute it.

### Operational simplicity

Prefer a simpler implementation that is inspectable and testable over an academically elegant one that is fragile in production. Reliability outranks sophistication.

## Target component model

### Connectivity service

A thin service responsible for establishing and maintaining IBKR sessions, authenticating to TWS or IB Gateway, and exposing a stable internal event stream. It must own reconnect logic, pacing-awareness, and heartbeat monitoring.

### Universe service

A scheduled service that discovers instruments, resolves contract identifiers, validates expiries/strikes/multipliers, and materializes the canonical option universe to storage.

### Collector service

A streaming or polling service that subscribes to underlyings and options, receives ticks and computations, stamps them with normalized timestamps, and writes them to the raw layer.

### Analytics service

A stateless computation layer that consumes normalized market states and emits forward curves, implied volatilities, surface snapshots, model prices, and Greeks.

### Portfolio/risk service

A service that merges positions with analytics snapshots, calculates sensitivities and scenario PnL, and publishes both intraday and end-of-day reports.

### Control plane

The job scheduler, configuration repository, logging, metrics collection, release registry, and runbooks that allow the whole system to be operated as a product rather than a loose script collection.

## Recommended deployment layout

A practical first implementation uses one repository, one shared configuration package, one relational metadata store, and one object store or partitioned file store for raw and derived datasets. The live collector should run continuously. The analytics and risk layers should run both incrementally intraday and as an end-of-day reconciliation batch. Keep the connectivity process separate from the analytics process. If the IBKR session drops, the collector can restart without invalidating the analytics layer or corrupting its state.

## Core naming conventions

- Use a stable composite instrument key: underlying symbol, security type, exchange, expiry, strike, option right, multiplier, currency, and the broker-specific contract identifier.
- Distinguish raw timestamps from normalized timestamps. For example: exchange_ts, receipt_ts, and canonical_ts.
- Version every configuration set: universe version, QC threshold version, solver version, and scenario-grid version.
- Use explicit maturity notation in years for all analytics, but store the original expiry date and the day-count convention used to convert maturity dates into year fractions.
