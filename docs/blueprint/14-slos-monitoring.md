> Source: blueprint PDF, pages 43–45. Faithful transcription — see ../blueprint/README.md for governance status.

# Part XIV — Service-level objectives, monitoring, and operational metrics

An institutional-grade platform should define service-level objectives even if the first deployment is modest in scale. The point is to make expectations explicit. Without SLOs, the team cannot distinguish between acceptable degradation and unacceptable operational failure. The SLOs below are representative and should be tuned to the actual business context, but the habit of defining them should exist from the first release.

| Service | Key metric | Suggested target | Operator action if missed |
|---|---|---|---|
| Connectivity | Heartbeat age | < 30 seconds during session | Investigate gateway, session auth, network |
| Collector | Raw-event write lag | < 5 seconds median | Check writer backlog and storage endpoint |
| Snapshots | Latest snapshot freshness | < configured cadence + tolerance | Inspect snapshot job and raw-event completeness |
| Forward engine | Maturity coverage ratio | > 95% of monitored maturities | Inspect quote QC and parity diagnostics |
| IV solver | Convergence ratio | > 97% on accepted quotes | Review solver residuals and quote quality |
| Surface engine | Completed monitored surfaces | > 95% on monitored underlyings | Inspect sparse maturities and fallback path |
| Scenario engine | Report completion before cut-off | 100% | Check dependency graph and runtime bottlenecks |
| QC framework | QC report publication | 100% daily | Investigate failed checks or orchestration issues |

### Recommended metrics catalog

- Collector metrics: raw events per second, reconnect count, heartbeat age, write lag, malformed-event count.
- Snapshot metrics: snapshots produced, stale-underlying ratio, stale-option ratio, fallback-spot ratio.
- Forward metrics: candidate count per maturity, confidence score distribution, outlier rejection rate.
- IV metrics: solve count, convergence ratio, median iterations, residual distribution, bounds-hit count.
- Surface metrics: accepted-point count, fit RMSE distribution, fallback-fit frequency, calendar-check failures.
- Risk metrics: aggregate delta, gamma, vega, theta by portfolio and by underlying, timestamp age.
- Scenario metrics: scenario runtime, result count, worst-case loss, top-contributor concentration.
- QC metrics: check pass/warn/fail counts, unresolved triage count, repeat-failure counts by underlying.

### Dashboard design

The operator dashboard should have three layers. The first layer is system health: are services running, is data flowing, are tables being written. The second layer is analytics health: are forwards stable, are IVs converging, are surfaces fitting. The third layer is risk/report health: are the latest risk and scenario reports current and complete. Do not overload the front page with too many charts. The dashboard should answer the operational questions in under one minute.
