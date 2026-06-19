> Source: blueprint PDF, pages 22–28. Faithful transcription — see ../blueprint/README.md for governance status.

# Part IV — Detailed implementation guides

## A. Python repository structure

A junior developer should not begin with a flat directory of scripts. The repository should be organized around clear ownership boundaries. One recommended structure is shown below. The exact names may vary, but the spirit of the separation of concerns should remain.

```
repo_root/
    configs/            # environment files, thresholds, calendars, scenario grids
    docs/               # runbooks, module READMEs, architecture diagrams
    src/
        connectivity/   # IBKR session management, reconnect logic, heartbeat
        universe/       # contract resolution, option-chain discovery, master tables
        collectors/     # raw event capture, polling/stream adapters
        snapshots/      # normalized market-state builders
        forwards/       # parity forward engine, carry diagnostics
        iv/             # pricing inversion and diagnostics
        surfaces/       # fitters, interpolation, no-arb checks
        pricing/        # European and American pricers
        risk/           # Greeks, aggregation, scenarios
        storage/        # schema definitions, read/write adapters
        orchestration/  # job entry points, scheduler wrappers
        qc/             # validation checks and anomaly detection
        utils/          # dates, calendars, math helpers, logging helpers
    tests/              # unit tests, integration tests, regression fixtures
    notebooks/          # strictly exploratory; never source-of-truth code
    scripts/            # operational wrappers for CLI entry points
```

### Coding standards

- Every module must expose typed functions or dataclasses for inputs and outputs.
- Do not pass loose pandas rows between modules as implicit contracts. Define explicit objects.
- Separate pure calculations from I/O. Pure functions are easier to test and to replay historically.
- Any function with hidden defaults affecting economics must document those defaults in the docstring.
- Every public module should contain one worked example that a junior engineer can run.

## B. IBKR integration map

The system should isolate broker-specific behavior behind a thin adapter. This makes the rest of the stack easier to test and protects the analytics layer from session-management details. The adapter should expose methods such as connect, disconnect, resolve_contract, request_option_chain, subscribe_market_data, cancel_market_data, and request_historical_bars. It should transform broker callbacks into normalized internal events.

| Need | IBKR-facing capability | Normalized output | Notes |
|---|---|---|---|
| Session management | connect / heartbeat / reconnect | session_state events | Own backoff and retry behavior in one place |
| Universe discovery | option-chain resolution and contract details | canonical instrument rows | Persist raw payloads for audit |
| Live market data | underlying and option quote subscriptions | raw quote events | Keep callbacks lightweight |
| Historical retrieval | bars or snapshots for replay/bootstrap | historical raw partitions | Use same schemas as live market data |
| Broker computations | option computation fields if available | diagnostic quote features | Never rely exclusively on broker analytics |

### Practical adapter pattern

Implement an adapter class that owns the broker connection and converts all callbacks into internal events. The rest of the codebase should never import broker callback enums directly. It should consume a broker-agnostic event stream. This pattern pays off immediately when writing replay tests because the replay source can emit the same internal event objects as the live adapter.

## C. Data schemas and table contracts

The platform should maintain a small number of carefully designed tables rather than a sprawl of one-off datasets. The tables below are the minimum recommended schema families.

| Table family | Purpose | Required primary keys |
|---|---|---|
| instrument_master | Canonical underlyings and option contracts | instrument_key, as_of_date |
| raw_market_events | Immutable tick/field observations | session_id, event_id |
| market_state_snapshots | Time-aligned inputs to analytics | snapshot_ts, instrument_key |
| forward_curve | Forward and implied carry diagnostics | snapshot_ts, underlying, maturity |
| iv_points | Solved implied-volatility observations | snapshot_ts, contract_key |
| surface_parameters | Model parameters by slice or global fit | snapshot_ts, underlying, maturity, model_version |
| surface_grid | Regularized grid values for use by other services | snapshot_ts, underlying, maturity, moneyness_bucket |
| pricing_results | Model price and Greeks by contract | snapshot_ts, contract_key, pricer_version |
| positions | Source-of-record positions or hypothetical positions | valuation_ts, portfolio_id, contract_key |
| risk_aggregates | Grouped risk outputs | valuation_ts, portfolio_id, group_key |
| scenario_results | Stress PnL outputs | valuation_ts, portfolio_id, scenario_id, contract_key |
| qc_results | Validation outcomes and diagnostics | run_id, check_name, target_key |

### Schema design rules

- Never store decimals as formatted strings. Use numeric types and explicit scales.
- Keep version fields on all derived analytics tables.
- Store timezone-aware timestamps or store UTC plus a clear convention; do not mix local and UTC ambiguously.
- Every derived table should reference the source snapshot_ts used to compute it.
- QC results must point to both the failing object and the run that produced the failure.

## D. Detailed quality-control checklist

The QC layer should be formalized as a library of named checks. Each check returns a status, a severity, the measured value, the threshold version applied, and a payload of context fields. The checks below should exist at minimum in the first production release.

- Collector continuity: no unexplained gap longer than N seconds during liquid session.
- Underlying quote health: spread percentage and staleness below configured thresholds.
- Option chain coverage: minimum count of eligible calls and puts per monitored maturity.
- Forward stability: weighted forward estimate within tolerance of the median candidate.
- Parity residual: per-strike residuals below threshold for the accepted set.
- IV solver convergence: convergence ratio above threshold and residual distribution acceptable.
- Surface fit error: root-mean-square fit error within threshold by maturity.
- Calendar sanity: total variance should not move backwards across neighboring maturities without a fail flag.
- Greek sanity: finite-difference and analytic Greeks agree within tolerance on test contracts.
- Scenario completeness: all configured scenarios executed and stored with no missing results.

### Robust outlier metrics

For several QC checks, robust statistics are preferable to means and standard deviations. For example, forward candidates and preliminary IV points often contain a few bad quotes. Median-based statistics resist contamination better and are easier to explain in ops reviews.

$$z_i = \frac{x_i - \mathrm{median}(x)}{1.4826 \, \mathrm{MAD}(x)}$$

*Equation 24. Robust z-score using median absolute deviation.*

## E. Testing strategy

The platform requires four layers of tests. First, unit tests for pure math and data-transformation functions. Second, integration tests for the broker adapter using mocks or recorded event streams. Third, regression tests using archived market-data snapshots and expected outputs. Fourth, operational tests that verify scheduler entry points, logging, and basic health-check behavior.

- Unit tests: pricing identities, inversion convergence, parity calculations, date-count logic, and scenario aggregation.
- Integration tests: event normalization, contract resolution, quote subscription setup, reconnect handling.
- Regression tests: known chains with frozen expected forward curves, IV points, and surface diagnostics.

## F. End-to-end processing sequence

The sequence below is the recommended canonical run order for one business day. It applies both to live production and to replay. The only difference being the source of events. In production, the source is the broker adapter. In replay, the source is the stored raw-event partition.

1. Start connectivity service and verify session health.
2. Refresh or validate instrument master for the session date.
3. Launch underlying and option collectors; begin writing immutable raw events.
4. Create normalized market-state snapshots on the configured cadence.
5. Build forward curves by maturity from the latest accepted snapshot.
6. Solve implied volatilities for the filtered option set.
7. Fit and publish surface slices and the cross-maturity grid.
8. Compute model prices and Greeks for all eligible contracts.
9. Join positions and calculate line-level and aggregate risk.
10. Run scenario engine and publish stress summaries.
11. Run QC suite and generate operator dashboard/report.
12. Archive artifacts, close the day, and prepare replay partitions.

## G. Pseudocode for key services

### Collector service pseudocode

```python
class RawCollector:
    def __init__(self, broker_adapter, writer, clock, config):
        self.adapter = broker_adapter
        self.writer = writer
        self.clock = clock
        self.config = config

    def on_event(self, broker_event):
        normalized = normalize_event(broker_event, receipt_ts=self.clock.now_utc())
        self.writer.append("raw_market_events", normalized)

    def run(self):
        self.adapter.connect()
        self.adapter.subscribe_underlyings(self.config.underlyings)
        self.adapter.subscribe_options(self.config.option_universe)
        self.adapter.set_callback(self.on_event)
        self.adapter.heartbeat_loop()
```

### Snapshot builder pseudocode

```python
def build_snapshot(events, snapshot_ts, config):
    state = latest_by_field_before(events, snapshot_ts)
    underlying = build_underlying_state(state, config)
    option_rows = build_option_rows(state, snapshot_ts, config)
    return MarketStateSnapshot(
        snapshot_ts=snapshot_ts,
        underlying_state=underlying,
        option_rows=option_rows,
        flags=derive_state_flags(underlying, option_rows, config),
    )
```

### Forward engine pseudocode

```python
def estimate_forward(snapshot, maturity, rate, config):
    candidates = []
    for strike in eligible_strikes(snapshot, maturity, config):
        call_mid = mid(snapshot.call_quote(strike, maturity))
        put_mid = mid(snapshot.put_quote(strike, maturity))
        if is_usable(call_mid, put_mid):
            f_i = strike + math.exp(rate * maturity.t) * (call_mid - put_mid)
            weight = liquidity_weight(snapshot, strike, maturity, config)
            candidates.append((strike, f_i, weight))
    cleaned = reject_outliers(candidates, method="mad", config=config)
    return weighted_average(cleaned)
```

### Surface engine pseudocode

```python
def fit_surface(iv_points, config):
    slices = []
    for maturity, pts in group_by_maturity(iv_points):
        x = [p.log_moneyness for p in pts]
        y = [p.total_variance for p in pts]
        fit = fit_svi_or_fallback(x, y, config)
        slices.append(fit)
    return interpolate_across_maturities(slices, config)
```

## H. Failure modes and responses

A junior operator must know what to do when the system deviates from nominal behavior. The table below intentionally translates technical issues into operational actions.

| Failure mode | Likely cause | Immediate response | Longer-term fix |
|---|---|---|---|
| Collector reconnect loop | Gateway session expired or network instability | Check session process, restart adapter, confirm client ID not duplicated | Harden heartbeat, improve process supervision |
| Sudden drop in eligible quotes | Entitlement issue, market closure, or stale quotes | Inspect collector summary and exchange status | Add exchange-calendar gating and stale-data diagnostics |
| Forward curve unstable at one maturity | Poor call-put parity from illiquid strikes | Inspect candidate strikes and QC reason codes | Tighten strike selection or outlier thresholds |
| Surface fit failure | Too few accepted points or pathological quote set | Publish fail flag and retain raw points for review | Improve fallback interpolation path |
| Greeks disagree with benchmarks | Unit convention drift or pricer regression | Run regression suite, freeze deployment | Strengthen test coverage and interface contracts |
| Scenario results missing | Upstream analytics incomplete or orchestration failure | Check dependency graph and rerun missing partitions | Add dependency-aware scheduling and alerting |

## I. Deployment model

The first institutional-grade deployment does not require exotic infrastructure. A modest but disciplined architecture is sufficient: one always-on collector process, one scheduler, one metadata database, one object or file store, and one monitoring stack. The key is process isolation. Keep the broker connectivity service separate from compute-heavy analytics jobs. This minimizes the chance that a CPU spike in analytics causes dropped market data.

- Development: local workstation, IBKR paper or non-production session, local file store, local scheduler.
- Staging: dedicated VM or small server, production-like configs, replay from stored data, full QC and dashboards.
- Production: dedicated collector host, separate analytics host if possible, supervised services, alerting, immutable artifact storage.

## J. Release management

Every change that can alter economics must have a release artifact. That includes solver tolerances, QC thresholds, scenario grids, and smoothing settings. The release should specify what changed, why it changed, what tests passed, and which historical periods were revalidated. Never deploy a mathematically meaningful change by editing a notebook and rerunning the daily job.
