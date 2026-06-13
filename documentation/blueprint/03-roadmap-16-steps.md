> Source: blueprint PDF, pages 10–21. Faithful transcription — see ../blueprint/README.md for governance status.

# Part III — Sixteen-step implementation roadmap

This part is the core delivery. Each step contains a practical objective, a task list, expected outputs, acceptance tests, and implementation notes. The steps are ordered deliberately. A junior developer should not skip ahead. Later stages depend on decisions made and artifacts created earlier. If a later step appears blocked, the correct remedy is usually to strengthen the preceding step rather than to patch around the problem.

## Step 1 — Access, environments, and security

### Objective

Create a safe, repeatable foundation for all later work. The junior developer must set up a development environment, a production-like environment, and a configuration/secret management pattern before writing any analytics code. A common failure mode in early trading infrastructure is to mix credentials, hand-edited notebooks, and ad hoc scripts. This step eliminates that risk by imposing structure from the beginning.

### Detailed tasks

(a) Install Python and package management tooling; pin versions; produce a reproducible lock file. (b) Stand up TWS or, preferably for unattended operation, IB Gateway in an isolated user account. (c) Configure an application-level client ID convention so multiple services do not collide. (d) Build secret loading via environment variables or a secret manager, never via hard-coded strings. (e) Create configuration files for exchanges, instruments, calendars, and QC thresholds. (f) Add basic health checks: API reachable, login valid, market-data entitlement present, and clock synchronization within tolerance. (g) Decide and document where logs and artifacts will be written in each environment.

### Outputs

A reproducible Python environment, a running IBKR session reachable from code, a configuration package under version control, and a bootstrap script that proves end-to-end connectivity without placing orders. The bootstrap script should print the session state, current time, contract resolution result for one underlying, and one market-data retrieval example.

### Acceptance criteria

A new machine can be provisioned from documentation; the bootstrap script succeeds; secrets are not stored in the repository; all environment-specific values are loaded through documented configuration; and a simple connectivity job can be run from the scheduler without manual intervention.

### Junior implementation notes

Start with the smallest useful smoke test. Resolve one contract, request one quote, and write one JSON line to disk. Once that succeeds reliably, only then add complexity. Keep a markdown runbook called environment.md updated as you go. Every manual step performed during setup must be documented immediately, otherwise the environment will become impossible to reproduce.

## Step 2 — Instrument master and universe discovery

### Objective

Build the canonical instrument master that every other service will use. The instrument master is the single source of truth for underlyings, option contracts, multipliers, currencies, exchanges, and expiries. Without this layer, analytics become brittle because the same real-world contract may be represented by inconsistent ad hoc identifiers across scripts.

### Detailed tasks

(a) Define the canonical schema for underlying instruments and option instruments. (b) Implement contract-resolution helpers that map human-readable requests into broker contract identifiers. (c) Use the option-chain discovery APIs to obtain expiries, strikes, trading class, and multiplier information. (d) Normalize expiries into a consistent date format and strikes into numeric values. (e) Persist both the raw broker response and the normalized canonical representation. (f) Add data-quality checks for duplicate contracts, impossible multipliers, or missing fields. (g) Version the discovered universe by date and configuration so the same trading day can always be reconstructed.

### Outputs

A canonical instrument master table plus helper methods: get_underlying(symbol), get_option_chain(symbol, date), resolve_contract(key), and load_active_universe(session_date). The table should support filtering by exchange, product type, maturity window, and listing status.

### Acceptance criteria

Given a configured underlying, the system can reproduce the same active option universe on repeated runs; duplicates are removed deterministically; multiplier and currency are always populated; and any unresolved contract is surfaced as an explicit exception with diagnostics rather than silently skipped.

### Junior implementation notes

Treat the broker's contract identifier as an external foreign key, not as your only identifier. Your canonical key must remain meaningful even if the broker session changes. Store raw contract payloads as evidence. Future debugging often depends on seeing exactly what the broker returned at discovery time.

## Step 3 — Market-data ingestion layer

### Objective

Capture raw underlying and option observations in a way that is robust to disconnects, pacing issues, and intermittent field availability. The raw layer is the evidentiary record of what the system saw. It must be append-only and loss-aware. If data is missing, that fact must be recorded rather than papered over.

### Detailed tasks

(a) Decide which subscriptions are persistent streaming subscriptions and which are snapshots. (b) Create a collector process for underlying quotes and another for options, or a unified collector with explicit partitioning. (c) Normalize incoming ticks into a common event structure with instrument key, field name, value, source timestamp if available, receipt timestamp, and collector timestamp. (d) Persist every event to the raw layer with a session identifier. (e) Add reconnect logic with backoff and heartbeat monitoring. (f) Detect market-data pacing or entitlement failures and log them as structured events. (g) Build daily collector summaries: event counts, missing intervals, reconnect count, and coverage ratios.

### Outputs

An append-only raw event store, a collector service process, health metrics, and a session summary report. The collector should emit enough metadata to reconstruct whether a downstream analytic used a fresh or stale observation.

### Acceptance criteria

The collector can run for an entire session without manual supervision; disconnects produce warnings and controlled recovery; a synthetic kill-and-restart test does not corrupt the raw store; and at least one day of data can be replayed from disk without reaching back to the broker.

### Junior implementation notes

Never compute analytics inside the collector callback itself. The callback should only normalize, stamp, and persist. Heavy logic inside the callback is the fastest path to dropped events and undebuggable behavior.

## Step 4 — Persistent storage and data model

### Objective

Define durable storage for immutable raw data and curated analytics. The storage design must support both live incremental writes and historical backfills. The same schemas should be used in both cases so that replay and live computation share code rather than diverge.

### Detailed tasks

(a) Choose a metadata store for configuration, jobs, and reference entities. (b) Choose a columnar partitioned store for large raw and derived datasets. (c) Design partitioning by trade date, underlying, and data layer. (d) Create schema definitions for raw events, normalized market-state snapshots, forwards, implied-vol points, surface parameters, model prices, Greeks, scenarios, positions, and QC results. (e) Enforce schema evolution rules and backfill compatibility. (f) Decide retention policy for raw tick/event data, normalized snapshots, and derived analytics. (g) Add write-ahead validation so malformed records are rejected early with explicit logs.

### Outputs

Versioned schemas, migration scripts, partitioned datasets, and a documented data lineage from raw events through curated analytics. The system should be able to answer the question 'which raw records produced this surface snapshot?' within one query or one notebook cell.

### Acceptance criteria

All required tables exist; partitioning supports efficient daily queries; replay and live writes land in identical schemas; and deleting/recomputing one derived partition does not require rewriting the raw layer.

### Junior implementation notes

Use simple, explicit schemas. Avoid storing nested structures unless they materially reduce complexity. A junior developer should favor readability and debuggability over compact cleverness. Do not mix timestamps in different time zones in the same field.

## Step 5 — Spot builder and market-state snapshots

### Objective

Transform raw ticks into coherent market-state snapshots for each underlying and each option chain. Snapshots are the deterministic inputs to all downstream analytics. They must be time-aligned, quality-labeled, and reproducible from raw events.

### Detailed tasks

(a) Define the snapshot frequency or trigger, for example every N seconds, every material update, or on demand. (b) For each snapshot, compute reference spot using mid-price when reliable and documented fallbacks when not. (c) Store bid, ask, last, spread percentage, and the chosen reference-type flag. (d) Join options into the same snapshot with the most recent eligible quote not older than a configurable age threshold. (e) Add market-state flags such as open/closed, stale underlying, stale option, or fallback spot. (f) Build snapshot completeness metrics per underlying and maturity.

### Outputs

A normalized market-state dataset keyed by timestamp, underlying, and option contract, including all fields required by the pricing and surface layers.

### Acceptance criteria

Given the same raw events and the same snapshot parameters, repeated runs produce identical market-state rows. Stale-option logic is visible in flags. Spot fallbacks are labeled, not hidden. Spot diagnostics are queryable by day and time.

### Junior implementation notes

Keep the snapshot builder pure: raw events in, snapshots out. Do not call external services from inside it. That purity makes it easy to replay past sessions and to unit-test the builder with synthetic event streams.

## Step 6 — Forward and implied carry engine

### Objective

Compute a robust forward for each maturity and derive an implied carry or dividend diagnostic curve. This engine is foundational because it determines the moneyness coordinate used by the implied-volatility and surface modules.

### Detailed tasks

(a) For each maturity, identify eligible call-put pairs near the money. (b) Compute call and put mids, then parity forward per strike. (c) Weight forward candidates by liquidity and quote quality. (d) Remove outliers using robust statistics such as median absolute deviation or parity residual thresholds. (e) Fit or smooth the forward term structure across maturities. (f) If a rate curve is available, derive implied carry/dividend yield and compare it with expectations. (g) Persist both the chosen forward and all diagnostics used to choose it.

### Outputs

A forward curve dataset by underlying and maturity plus a diagnostics dataset listing candidate strikes, weights, residuals, and quality labels.

### Acceptance criteria

The forward is stable across small perturbations in the eligible strike set; outlier pairs do not dominate the estimate; diagnostics can explain why a maturity was flagged poor quality; and the forward term structure behaves sensibly across adjacent maturities unless explicitly flagged as unreliable.

### Junior implementation notes

Debug the forward engine first on a handful of liquid maturities before scaling to the whole chain. When a maturity fails, inspect the raw quotes rather than trying to tune the smoother blindly. In practice, most forward errors originate in quote quality, not in the formula.

## Step 7 — Quote normalization and quality control

### Objective

Establish a defensible process for deciding which option quotes may enter the solver and surface layers. The point is not to maximize quote count; the point is to maximize the number of quotes that are economically meaningful and consistent with a tradable state.

### Detailed tasks

(a) Compute standard quote-quality features: spread percentage, bid positivity, quote age, open interest, volume, and monotonicity hints. (b) Mark quotes as usable, caution, or reject. (c) Detect crossed or locked markets, impossible prices relative to intrinsic value, and stale last prices. (d) Apply robust outlier statistics to parity residuals or preliminary implied volatilities. (e) Store the reason code for each rejected quote. (f) Keep both the full raw snapshot and the filtered snapshot so QC decisions are auditable.

### Outputs

A filtered quote set ready for inversion plus a QC table explaining every rejection or downgrade.

### Acceptance criteria

The same quote is either consistently accepted or rejected under a fixed threshold version. QC reason codes are exhaustive and mutually understandable. The filtered chain retains enough points for a stable surface while removing obvious garbage.

### Junior implementation notes

Do not implement QC as a monolithic if-statement. Break it into named checks and log each check separately. This makes threshold tuning and postmortem analysis far easier.

## Step 8 — Implied-volatility inversion engine

### Objective

Convert filtered option prices into implied volatilities using robust numerical methods. The engine must support diagnostics, error handling, and deterministic behavior. Every solved IV should be accompanied by enough metadata to understand how reliable it is.

### Detailed tasks

(a) Implement European inversion using a bracketed root solver for products where Black-style pricing is appropriate. (b) For American options, either invert through the chosen American pricer or compute a proxy IV under a documented convention. (c) Use intrinsic-value and no-arbitrage bounds to detect unsolvable inputs. (d) Record convergence status, iteration count, final residual, lower and upper bracket, and the pricing model used. (e) Expose fallback behavior for short-dated or near-intrinsic cases. (f) Build a batch interface that can solve an entire chain efficiently.

### Outputs

A table of implied-vol points with coordinates in strike, moneyness, maturity, delta if available, and extensive diagnostics.

### Acceptance criteria

Most liquid quotes converge cleanly; pathological cases are explicitly labeled; the solver passes reference examples; and finite perturbations in input price produce plausible IV changes rather than numerical explosions.

### Junior implementation notes

Write the scalar solver first, then the vectorized batch wrapper. Keep the scalar path readable and test it thoroughly. Most production bugs are easiest to find in the scalar implementation before optimization layers are added.

## Step 9 — Surface engine and parameter storage

### Objective

Build volatility surfaces by maturity and across maturities, while keeping the raw solved points and the final fitted representation separate. The surface module should produce both a machine-friendly fitted form and a human-debuggable diagnostics package.

### Detailed tasks

(a) Group implied-vol points by maturity. (b) Transform points into log-moneyness and total variance. (c) Fit the chosen parameterization slice by slice or apply the chosen nonparametric smoothing method. (d) Interpolate across maturities in variance space. (e) Compute goodness-of-fit metrics and no-arbitrage diagnostics. (f) Save both fitted parameters and reconstructed grid values. (g) Store rejected points and fit warnings so operators can see whether a smooth surface was built on very sparse input.

### Outputs

Surface parameter tables, a regularized surface grid, fit diagnostics, and quality flags by maturity and by underlying.

### Acceptance criteria

The fitted surface reproduces accepted market points within tolerance, diagnostics reveal sparse or poor-quality fits, and repeated runs under the same inputs return the same parameters. At least one plotting utility can visualize the raw points versus fitted slices for operator review.

### Junior implementation notes

Never discard the raw solved points after the fit. Operators must be able to compare the fitted surface with the exact points that entered the calibration. This is essential for debugging suspicious Greeks or scenario outputs later.

## Step 10 — Pricing engine

### Objective

Provide reusable pricing services for both European and American options. The pricing engine is the only module allowed to translate a state vector (spot, forward, maturity, volatility, carry parameters) into price and Greeks. Centralizing this logic prevents drift across notebooks and services.

### Detailed tasks

(a) Implement a European pricer consistent with the inversion engine. (b) Implement an American pricer suitable for single-name use. (c) Expose first-order and second-order Greeks, either analytically when available or numerically with documented finite-difference settings. (d) Create a clean Python API that accepts a typed input object and returns a typed result object. (e) Add benchmark tests and performance tests. (f) Document unit conventions rigorously.

### Outputs

A pricing package with scalar and vectorized interfaces, complete docstrings, examples, and benchmark fixtures.

### Acceptance criteria

Reference cases match expected values; the European and American engines agree in regimes where they should; unit tests cover sign conventions and limiting cases; and the API is stable enough to be imported by analytics, risk, and scenario modules without special casing.

### Junior implementation notes

Avoid embedding pricing logic directly in dataframes or notebooks. Keep the pricer a clean library with tests. The clearer this boundary is, the safer later refactors become.

## Step 11 — Greeks and per-position risk analytics

### Objective

Turn the pricing layer into a per-contract and per-position risk service. The output of this step should be the canonical risk snapshot used everywhere else in the system, including scenario analysis and operational dashboards.

### Detailed tasks

(a) Define the sensitivity set required at instrument level and at portfolio level. (b) Pull in the latest positions or hypothetical positions from the source of record. (c) Join positions to analytics snapshots and compute per-line price, Greeks, and monetized sensitivities. (d) Aggregate by instrument, maturity, underlying, and any desk-defined grouping keys. (e) Compute reconciliation checks against broker-returned Greeks if available. (f) Publish both line-level and aggregate outputs.

### Outputs

Position-level risk tables, aggregated risk tables, and reconciliation reports.

### Acceptance criteria

The same positions on the same analytics snapshot always produce the same aggregate risk; dollar gamma and dollar vega conventions are documented and stable; and reconciliation discrepancies beyond threshold are surfaced automatically.

### Junior implementation notes

Store both contract-level and aggregate outputs. Aggregates are convenient, but debugging always starts at the line level. If a total Greek looks wrong and the line-level breakdown is missing, the system becomes opaque immediately.

## Step 12 — Scenario engine and margin-style diagnostics

### Objective

Build a scenario framework that approximates worst-case losses under configured spot, volatility, and time shocks. The purpose is generic risk control, capacity planning, and margin-style diagnostics. It should not encode any strategy logic.

### Detailed tasks

(a) Define versioned scenario grids: spot up/down moves, volatility shifts, curve rotations if required, and time roll-down. (b) Reprice the full portfolio under every scenario. (c) Attribute PnL by line, underlying, and scenario family. (d) Compute worst-case loss, top contributors, and pathwise diagnostics. (e) Support both full repricing and local Greeks-based approximations for speed, while keeping the full repricing as the reference. (f) Persist the exact scenario set used for every report.

### Outputs

Scenario result tables, worst-case summaries, and margin-style approximation reports.

### Acceptance criteria

A report can be regenerated exactly given the positions, analytics snapshot, and scenario version. Worst-case contributors are explainable. Repricing and Greeks approximations agree within documented limits for small shocks.

### Junior implementation notes

Version the scenario grid. Do not leave it as a mutable notebook cell. The scenario definition is part of the data lineage and must be queryable alongside the output.

## Step 13 — Historical reconstruction and replay

### Objective

Enable the system to reconstruct historical analytics from stored raw data or historical snapshots using the same code path as live analytics. This step turns the infrastructure from a live monitor into a real research and audit platform.

### Detailed tasks

(a) Define how a historical day is replayed: from raw events into snapshots, from snapshots into forwards and surfaces, and from surfaces into risk. (b) Ensure all derived jobs can run in batch mode over a date range. (c) Detect missing raw partitions and create partial-data flags. (d) Store restated outputs in versioned partitions so newer code versions do not silently overwrite older historical analytics. (e) Compare replay outputs with live outputs for overlapping periods.

### Outputs

Replay scripts, backfill jobs, replay QA reports, and a historical archive of derived analytics.

### Acceptance criteria

At least one historical month can be reconstructed end to end. Replay and live outputs align on overlapping dates when using the same code version. Missing data is flagged rather than masked by interpolation without notice.

### Junior implementation notes

The replay code path should call the same libraries as live processing. Resist the temptation to fork a separate 'historical only' implementation. Dual code paths always drift and become inconsistent.

## Step 14 — Validation framework and anomaly detection

### Objective

Create the automated controls that determine whether each daily analytics run is trustworthy. Validation must be treated as a product, not as a last-minute dashboard. The framework should produce actionable flags instead of vague 'data looked weird' statements.

### Detailed tasks

(a) Define validation checks for coverage, stale-data rates, forward stability, solver convergence, surface smoothness, no-arbitrage diagnostics, and reconciliation deltas. (b) Build daily reports summarizing pass/warn/fail outcomes. (c) Add anomaly detection against rolling baselines for key metrics such as quote counts, forward residuals, fit errors, and scenario losses. (d) Write every failed record to a triage table with enough context to investigate. (e) Define escalation thresholds and who gets notified.

### Outputs

QC reports, anomaly tables, a validation dashboard, and triage views for failed instruments or maturities.

### Acceptance criteria

A daily operator can identify the failing underlyings or maturities within minutes. Every failed validation has a reason code and supporting context. Historical trends of QC metrics are visible for regression monitoring.

### Junior implementation notes

A good validation framework is specific. Avoid generic red banners. Operators need to know which maturity failed, which quote count collapsed, which solver residual blew out, and where to look next.

## Step 15 — Orchestration, logging, and observability

### Objective

Turn the build into an operable system with schedules, retries, metrics, and alerting. A correct algorithm that cannot be monitored is not production-ready.

### Detailed tasks

(a) Define jobs for universe refresh, live collection, incremental analytics, end-of-day reconciliation, replay, and QC. (b) Add structured logging with correlation IDs linking collector sessions to analytics jobs. (c) Expose metrics such as event rates, stale ratios, forward failures, solver failure counts, and scenario run times. (d) Create alerts for collector death, missing partitions, elevated failure rates, and QC fails. (e) Implement restart procedures that do not duplicate or corrupt records. (f) Build simple dashboards that show system health and latest analytics coverage.

### Outputs

Scheduled jobs, logs, dashboards, and alert routes.

### Acceptance criteria

A simulated failure of the collector or analytics service is detected within a documented interval. Restarting a failed job does not silently duplicate outputs. Operators can identify the last healthy run and current backlog instantly.

### Junior implementation notes

Prefer fewer well-labeled metrics over many opaque ones. An operator should be able to answer: Is data flowing? Are surfaces building? Are QC checks passing? Are scenario reports current? Start there and expand only if needed.

## Step 16 — Production hardening, documentation, and handover

### Objective

Finish the infrastructure as a maintainable product. The last step is not code; it is ensuring that another person can operate, support, and extend the platform without reverse-engineering it.

### Detailed tasks

(a) Freeze interface contracts and publish them in documentation. (b) Create onboarding notes, runbooks, deployment instructions, and a release checklist. (c) Record known limitations and future enhancements. (d) Add ownership and support expectations. (e) Define change-management rules for schema changes, threshold changes, and model changes. (f) Conduct a handover walkthrough where the junior engineer demonstrates operation from environment setup through QC interpretation.

### Outputs

A maintained documentation set, a release checklist, SOPs, and a support model.

### Acceptance criteria

A new engineer can set up the environment, run a connectivity smoke test, trigger a replay, read the QC report, and explain where to investigate a failed surface build without support from the original author.

### Junior implementation notes

Documentation is not an afterthought. Treat it like code. Every module should have a README, every public function should have docstrings, and every recurring operational procedure should exist as a runbook with concrete commands and screenshots where useful.
