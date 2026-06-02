# Workstream E — Integration and operations

- **Branch:** `feat/integration-ops`
- **Owns:** `src/orchestration`, `src/qc`, the Nautilus actor module, `docs/`.
- **Roadmap coverage:** steps 13 (historical reconstruction/replay), 14 (validation framework), 15 (orchestration and observability), 16 (production handover), plus the canonical run sequence (Part IV.F) and the operational runbooks (Part VI).
- **Depends on:** A (contracts), B (event stream), C and D (their frozen interfaces). Converges last.
- **Blocks:** nothing — this closes the loop.

## Objective

Wire the pieces into an operable product and prove the four invariants hold
end to end. The early parts (QC skeleton, scheduler scaffolding, runbook docs)
need only A's contracts and can start immediately; the actor and replay need B, C,
and D to have landed their interfaces.

## What you build

1. **The Nautilus actor** (the single glue piece). It feeds market state into C's
   and D's pure functions and writes their outputs — IV points, surface
   parameters, pricing, risk, scenarios — to A's storage with A's provenance
   stamps. Because the same actor runs in Nautilus's live and backtest engines,
   surfaces and risk recompute identically live and in replay. The actor holds no
   math of its own; it only transports and stamps.

2. **Orchestration and observability** (step 15). Jobs for universe refresh, live
   collection, incremental analytics, end-of-day reconciliation, replay, and QC.
   Structured logging with correlation IDs linking collector sessions to analytics
   jobs. Metrics (event rates, stale ratios, forward failures, solver failure
   counts, scenario run times). Alerts for collector death, missing partitions,
   elevated failure rates, QC fails. Restart procedures that do not duplicate or
   corrupt records. A small dashboard answering: is data flowing, are surfaces
   building, are QC checks passing, are scenario reports current. Implement the
   canonical end-of-day run sequence (Part IV.F).

3. **Validation / QC framework** (step 14). A library of named checks (Part IV.D):
   collector continuity, underlying quote health, option-chain coverage, forward
   stability, parity residual, IV solver convergence, surface fit error, calendar
   sanity, Greek sanity, scenario completeness. Each returns status, severity,
   measured value, threshold version, and a context payload, written as `QcResult`
   pointing at both the failing object and the run. Daily pass/warn/fail report,
   anomaly detection against rolling baselines, a triage table, escalation
   thresholds. Be specific — name the failing maturity/quote/solver, not a generic
   red banner.

4. **Historical reconstruction and replay** (step 13). Replay a stored day from
   raw events into snapshots, forwards, surfaces, and risk using the identical
   code path as live. Run all derived jobs in batch over a date range; flag missing
   partitions; write restated outputs to versioned partitions so newer code never
   silently overwrites older analytics; compare replay outputs to live for
   overlapping periods.

5. **Handover** (step 16). Freeze and publish the interface contracts; write the
   five operational runbooks (start of day, intraday health, end of day,
   replay/backfill, incident response — Part VI); release-management rules (every
   economics-affecting change gets a release artifact: what changed, why, tests
   passed, periods revalidated); known limitations and support model.

## Acceptance criteria

- A simulated collector/analytics failure is detected within a documented interval;
  restarting a failed job does not duplicate outputs; operators can identify the
  last healthy run and current backlog instantly.
- At least one historical month reconstructs end to end; replay and live outputs
  align on overlapping dates under the same code version; missing data is flagged,
  not masked by silent interpolation.
- A daily operator can find the failing underlyings/maturities within minutes;
  every failed validation has a reason code and supporting context.
- A new engineer can set up the environment, run a connectivity smoke test, trigger
  a replay, read the QC report, and explain where to investigate a failed surface
  build — without the original author.

## Test surface

Cross-cutting rules live in [TESTING.md](TESTING.md) — read it first. You converge
last and you verify everyone else's invariants, so two of your tests are the
system's headline guarantees and must be real, not prose.

Same-code-path replay (the headline):
- Drive the actor once from a simulated live event stream and once from the same
  events replayed off stored raw partitions; assert the derived outputs
  (snapshots, forwards, surfaces, risk) are byte-identical. This is the test the
  whole architecture exists to pass — it is not optional and not a smoke check.

Provenance verification (your invariant to enforce):
- A cross-cutting test that walks every C/D output landing in storage and asserts
  a non-empty, well-formed provenance stamp — the determinism and provenance the
  other workstreams claim, checked rather than trusted.

Orchestration and replay robustness:
- Kill a job mid-run and restart: no duplicated and no corrupted outputs;
  operators can identify the last healthy run and the current backlog from the
  recorded state.
- A missing partition is flagged explicitly, never masked by silent interpolation.
- Restated outputs write to versioned partitions: a newer-code run does not
  overwrite an older analytic — assert the old partition survives alongside the
  new.
- A simulated collector/analytics failure is detected within the documented
  interval (injected clock, not a real wait).
- Correlation IDs link a collector session to its analytics jobs — assert the
  trace resolves end to end.

QC framework — each named check (collector continuity, underlying quote health,
chain coverage, forward stability, parity residual, IV convergence, surface fit
error, calendar sanity, Greek sanity, scenario completeness) has:
- a passing fixture and a failing fixture, and
- on failure, a `QcResult` carrying status, severity, measured value, threshold
  version, and a context payload that names the specific failing
  maturity/quote/solver — assert the specificity, since a generic red banner is
  the failure mode this check exists to prevent.
- Anomaly detection flags an injected spike against a rolling baseline.

Handover acceptance: the "new engineer" criterion is a scripted end-to-end test
where possible — environment bootstrap, connectivity smoke test, a triggered
replay, and a generated QC report all run from documented commands on a fresh
checkout.

## Invariants you own

Same-code-path replay is your headline acceptance test — achievable only because
the actor drives the identical pure functions whether events come from B live or
from stored raw partitions. You verify (via QC) that the determinism and
provenance stamps the other workstreams produce are actually present and correct.

## Gotchas

Resist forking a separate "historical only" code path — dual paths always drift.
Prefer fewer well-labeled metrics over many opaque ones. Documentation is part of
the change, not a follow-up: every module has a README, every public function a
docstring, every recurring procedure a runbook with concrete commands.
