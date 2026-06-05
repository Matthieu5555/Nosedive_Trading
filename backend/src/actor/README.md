# actor — the glue that drives C/D and stamps their outputs

TL;DR. The actor is the one piece that turns raw market events into every derived
analytic the platform stores. It holds no math of its own. It transports market
state into Workstream C's and D's pure functions (snapshots, forwards, IV,
surfaces, pricing, risk, scenarios), stamps each output with provenance, and writes
it to A's storage. Because the same actor runs over a live event stream and over the
same events replayed off disk, surfaces and risk recompute identically live and in
replay — that one-code-path property is the whole reason this module exists.

Fastest path: replay one stored day through the actor and persist the outputs.

```python
from datetime import datetime, timezone
from actor import run_day

outputs = run_day(
    store,                       # A's ParquetStore, already holding the day's raw events
    trade_date,                  # the date to replay
    positions,                   # the portfolio's positions (may be empty)
    instruments=universe_keys,   # the InstrumentKeys to build snapshots for
    masters=instrument_masters,  # InstrumentMaster per contract (strike/right/expiry/multiplier)
    config=platform_config,
    config_hash=config_hash,     # the hash stamped into every output
    as_of=datetime(2026, 5, 29, 15, 30, tzinfo=timezone.utc),   # market/valuation time
    calc_ts=datetime(2026, 5, 29, 16, 0, tzinfo=timezone.utc),  # computation time, stamped
    correlation_id="session-123",  # ties this run to the collector session in the logs
    persist=True,
)
```

`run_day` reads the day's raw events in canonical order via `collectors.replay_day`,
feeds them to `run_analytics`, persists when `persist=True`, and returns the
`ActorOutputs` either way.

## Data flow: market state in, stamped outputs out

This diagram shows the path of one as-of instant through the actor: raw market state
enters on the left, flows through C's and D's pure functions in the middle, and leaves
as stamped contracts persisted on the right. It omits the provenance stamp on each
output (covered below), the QC verdicts that ride beside the snapshots (those go to the
QC plane, not into `ActorOutputs`), and the structured log lines.

```text
                          run_analytics  (pure: no I/O, no clock)
 raw events ──┐   ┌─────────────────────────────────────────────────┐
 positions ───┼──▶│ build_snapshots ─▶ usable subset                 │
 as_of ───────┤   │       │                                          │
 calc_ts ─────┘   │       ├─▶ estimate_forward ─▶ ForwardEstimate    │
 (injected)       │       │        │  (rich; carries discount factor)│
                  │       │        ▼                                 │
                  │       ├─▶ solve_iv ─▶ iv_point                    │
                  │       │        │                                 │
                  │       │        ▼                                 │
                  │       ├─▶ fit_slice ─▶ SliceFit (rich)           │
                  │       │        │                                 │
                  │       ▼        ▼                                 │
                  │   resolve_valuation_inputs  (the join, math-free) │
                  │       │                                          │
                  │       ▼                                          │
                  │   position_risk ─▶ net_lots ─▶ aggregate_lines   │
                  │                  └─▶ scenario_line_pnls          │
                  └───────────────────────┬──────────────────────────┘
                                          ▼
                              ActorOutputs (8 frozen tuples)
                                          │
                       persist_outputs    ▼
                          store.write per table ─▶ A's Parquet/DuckDB
```

The entry point is `run_day` (or the live path): it reads the day's raw events off the
immutable raw layer and hands them, with the injected `as_of`/`calc_ts`, to
`run_analytics`. Inside, `build_snapshots` cleans the quotes and marks a usable subset;
that subset feeds the forward, IV, and surface fits in turn, each keeping a rich
in-memory result. The valuation join is the one point where C's snapshot, forward, and
surface objects meet D's input — it reads those rich results (not the persisted
contracts) and produces one `ContractValuationInput` per held contract, which D's risk
and scenario functions price. Everything `run_analytics` returns is an `ActorOutputs`
of eight frozen tuples; `persist_outputs` then routes each tuple to its table. The whole
middle box is pure, which is what lets the replay test compare two runs as values.

## Three entry points, separated on purpose

`run_analytics(events, positions, ...) -> ActorOutputs` is the pure compute step. It
takes raw events and positions and returns every derived contract for one as-of
instant. It touches no I/O and reads no clock — `as_of` (the market time) and
`calc_ts` (the computation time stamped into every output) are injected. The same
inputs always return an equal `ActorOutputs`, and the result is invariant to the
order of the events and positions, because the pure functions and `net_lots`
guarantee it. A run with nothing to compute returns an `ActorOutputs` whose tuples
are all empty, never a half-built object.

`persist_outputs(store, outputs)` is the write step. It routes each non-empty output
tuple to its table via `contracts.table_for_contract` and writes through A's
validated `store.write`. The derived tables are replace-semantics, so re-persisting a
recomputed as-of replaces just those partitions; persisting the same outputs twice
leaves identical bytes.

`run_day(store, trade_date, positions, ...)` is the disk entry point that composes
the two: read the raw layer, compute, persist. The live path differs only in that a
broker session populated the raw layer first; it then calls this same function. That
is what keeps live and replay one code path rather than two that drift.

Compute is split from persistence so the headline replay test can compare two runs
as values — a plain `==` over the frozen `ActorOutputs` dataclasses — instead of
diffing Parquet bytes. The bytes follow from the values once persisted.

## The pipeline, in the order outputs are produced

For one as-of instant `run_analytics` runs five stages, and the `ActorOutputs` fields
are in this order: snapshots, forwards, IV points, surface parameters, surface grid,
pricings, risk aggregates, scenarios.

1. Build snapshots over the observed events. Reserved `__`-prefixed meta-events
   (gaps) are dropped first via `collectors.is_observation` — a gap is data about
   absence, not a quote. The full snapshot set is persisted; the QC-usable subset
   feeds everything downstream.
2. Per `(underlying, maturity)` with usable option pairs, estimate the forward,
   anchored to the underlying's usable spot so the carry is implied. Keep the rich
   `ForwardEstimate` (it carries the discount factor the join needs) and project the
   usable ones to a `ForwardCurvePoint`.
3. Per usable option quote, solve the implied vol against its maturity's forward and
   project the converged ones to an `IvPoint`. An unconverged solve is labeled by the
   solver and simply not emitted.
4. Per maturity, fit a slice over its IV points. Keep the rich `SliceFit` for the
   join; project a slice that actually fit a curve to `surface_parameters` and grid
   cells (an `insufficient` slice has nothing to plot).
5. Resolve one valuation input per held contract (the join, below), then run D's
   risk and scenario pipelines, stamping each `PricingResult`, `RiskAggregate`, and
   `ScenarioResult` with the injected `calc_ts`.

The actor derives `maturity_years` from each option's expiry under ACT/365 (a
fixed-365 calendar-day count), the one day-count threaded through the forward and
surface projections so the persisted maturity agrees with what was solved against.

## The valuation join

D's pure risk core does not join C's snapshot, forward, and surface objects itself —
it takes one resolved `ContractValuationInput` per contract (ADR 0006 decision 1).
Building that input is the actor's job, and it is the only place C's contracts meet
D's input. `resolve_valuation_inputs(positions, snapshots=, forwards=, slices=,
masters=, exercise_style_for=) -> dict[str, ContractValuationInput]` does it, keyed by
`contract_key` and deduplicated across lots (two lots of one contract share one market
state, which is also what `net_lots` requires).

It is pure transport. Every field is a copy off C's rich in-memory results, with
exactly three definitional conversions: log-moneyness `k = ln(strike / forward)` to
read the surface, the implied carry C already computed, and `vol = sqrt(w / T)` to
turn the surface's total variance into the pricer's volatility. It never prices,
bumps, or re-fits. It reads the *rich* in-memory results from the same run, not the
persisted contracts, because the persisted `ForwardCurvePoint` drops the discount
factor and the persisted snapshot drops its QC verdict — both of which the join needs.

A contract that cannot be completed raises `ValuationJoinError` naming the contract
and the missing piece (no master, no usable underlying snapshot, no usable forward,
no fitted slice) — never a silent skip or a `NaN`. A low-confidence quote is priced
and labeled `CONFIDENCE_LOW`, not dropped, per D's convention.

Exercise style is the one fact no A contract carries (`InstrumentKey` has no style
field), so the caller injects a policy — a callable from instrument key to
`"european"`/`"american"` — defaulting to European via `default_exercise_style`.

## Provenance

Every derived output carries a stamp. C's `build_snapshots`, forward, IV, and surface
adapters take the injected `calc_ts`/`config_hash` and build their own stamps. C's
`pricing_result` and D's `risk_aggregate`/`scenario_result` take a pre-built stamp,
which the actor assembles via `stamping.build_stamp` with the *same* injected
`calc_ts`. Nothing here reads a clock, which is exactly what makes the stamps — and
therefore the outputs — reproducible across a live run and a replay.
