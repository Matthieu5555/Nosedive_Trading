# Workstream A — Foundation and data platform

- **Branch:** `feat/foundation`
- **Owns:** the typed-contracts/config package, `backend/src/storage`, `configs/`, the shared test-fixture library, the `pytest`/`ruff`/`mypy` gate, the environment bootstrap.
- **Roadmap coverage:** steps 1 (env/security) and 4 (persistent storage and data model), plus the data model the other workstreams sit on.
- **Depends on:** nothing. This is the keystone.
- **Blocks:** B, C, D, E. At minimum the contracts and fixtures must land before the others can fan out.

## Objective

Build the one thing every other workstream shares: the typed data contracts, the
config/versioning/provenance layer, the storage schemas and read/write adapters,
the fixture library, and the quality gate. Get the contracts right and frozen
early — every later edit to them ripples to four agents.

You own all shared contracts. Nobody else edits them. A contract change is a
request routed to you, never an in-place edit in another workstream.

## What you build

1. **Typed contracts** — one immutable dataclass per table family below. These are
   the only objects allowed to cross a workstream boundary. No loose pandas rows.
   Match the primary keys exactly (Part IV.C of the roadmap).

   | Dataclass | Primary key | Notes |
   |-----------|-------------|-------|
   | `InstrumentMaster` | instrument_key, as_of_date | composite key + raw broker payload kept as evidence |
   | `RawMarketEvent` | session_id, event_id | append-only; carries the three timestamps |
   | `MarketStateSnapshot` | snapshot_ts, instrument_key | reference spot + flags + completeness |
   | `ForwardCurvePoint` | snapshot_ts, underlying, maturity | chosen forward + diagnostics bundle |
   | `IvPoint` | snapshot_ts, contract_key | iv, k, total variance, solver diagnostics |
   | `SurfaceParameters` | snapshot_ts, underlying, maturity, model_version | SVI params + fit diagnostics |
   | `SurfaceGrid` | snapshot_ts, underlying, maturity, moneyness_bucket | regularized total-variance grid |
   | `PricingResult` | snapshot_ts, contract_key, pricer_version | price + Greeks + monetized Greeks |
   | `Position` | valuation_ts, portfolio_id, contract_key | source-of-record or hypothetical |
   | `RiskAggregate` | valuation_ts, portfolio_id, group_key | grouped sensitivities |
   | `ScenarioResult` | valuation_ts, portfolio_id, scenario_id, contract_key | stress PnL |
   | `QcResult` | run_id, check_name, target_key | status, severity, measured value, threshold version, context |

2. **Naming conventions, encoded once.** The composite instrument key (underlying
   symbol, security type, exchange, expiry, strike, option right, multiplier,
   currency, broker contract id). The three distinct timestamps `exchange_ts`,
   `receipt_ts`, `canonical_ts`. Maturity expressed in years for analytics but with
   the original expiry date and day-count convention stored alongside it.

3. **Config and versioning.** A validated config object (no hardcoded economics
   scattered in modules). Four independent version stamps: universe, QC-threshold,
   solver, scenario. A `config_hash` derived from the active config.

4. **Provenance stamper.** A helper every derived object passes through, recording
   source timestamps, calculation timestamp, code version, config hash, and the
   source records used. Determinism and transparency live here.

5. **Storage schemas and adapters.** DuckDB-over-Parquet. Partition by trade date,
   underlying, and data layer. Read/write adapters keyed to the contracts above.
   Write-ahead validation that rejects malformed records early with explicit logs.
   Replay and live writes land in identical schemas. Deleting/recomputing one
   derived partition must not require rewriting the raw layer.

6. **Fixture library** (Part IV.E). A small immutable set: liquid chains,
   pathological chains, and synthetic cases whose answers are analytically
   obvious. This is shared test ground — C and D extend it, you seed it. Seed the
   exact minimum set enumerated in the Test surface below; C and D bind their
   edge-case tests to those fixture names, so the pathologies must exist by name.

7. **Quality gate and bootstrap.** `uv add --dev pytest ruff mypy`; wire
   `uv run ruff check . && uv run mypy . && uv run pytest -q`. A bootstrap that
   proves a reproducible environment. (The IBKR connectivity smoke test of step 1
   is handed to Workstream B, which owns the broker session.)

## Acceptance criteria

- Every contract is an importable typed dataclass with its documented primary key,
  numeric types (never decimals-as-strings), version fields on all derived tables,
  timezone-aware-or-explicit-UTC timestamps, and a reference back to the source
  `snapshot_ts` on every derived object.
- Lineage works: "which raw records produced this surface snapshot?" is answerable
  in one query or one notebook cell.
- The fixture library loads and the quality gate runs green on an empty repo.
- Schema-evolution and backfill-compatibility rules are written down.

## Test surface

Cross-cutting rules — independent oracles, determinism mechanism, seam tests,
the edge-case floor, coverage — live in [TESTING.md](TESTING.md). Read it first.
You own the machinery the other four lean on, so your tests are load-bearing.

Contract validation, per dataclass:
- Round-trip: construct → write → read returns an equal object.
- Rejection (write-ahead validation, explicit error not coercion): missing a
  primary-key field; a numeric stored as a decimal-string; a naive (non-tz)
  datetime; NaN/inf in a numeric field; a negative value where positivity is
  required; a derived object with no `snapshot_ts` back-reference.
- Type assertion: stored numerics read back as `float`/`int`, never `str`.

Provenance stamper:
- A stamp carries every required field (source timestamps, calc timestamp, code
  version, config hash, source record ids) — assert on the full shape.
- Determinism: identical inputs → identical stamp; reordering the source-record
  list → identical stamp (or, if order is defined as significant, assert the
  order is enforced — pick one and test it).

Config and `config_hash`:
- Cross-process stability: the hash of a fixed config is identical computed in two
  separate processes, without relying on `PYTHONHASHSEED` (see TESTING.md).
- The four version stamps are independent: bumping `solver_version` leaves
  `universe`, `qc_threshold`, and `scenario` versions and their slice of the hash
  unchanged; changing any economics-bearing config field changes `config_hash`.

Storage adapters:
- Append-only enforcement: a write that would overwrite an existing raw
  observation is rejected; no downstream layer can mutate an upstream one.
- Partition isolation: deleting/recomputing one derived partition leaves the raw
  layer and other partitions byte-unchanged.
- Live and replay writes land in an identical schema — assert schema equality.
- Lineage: "which raw records produced this surface snapshot?" resolves in one
  query against a seeded fixture and returns the expected record set.
- Schema evolution: a partition written by the prior schema is still readable
  after adding a new nullable column; the backfill-compatibility rules you write
  down each have a test.

Fixture library — seed exactly this minimum set, each as a named, importable,
immutable fixture (C and D bind their edge-case tests to these names, per
TESTING.md):
- Two or three liquid chains with sane two-sided quotes.
- A crossed/locked-quote chain (bid > ask).
- A zero-bid / one-sided-quote chain.
- A single-strike maturity (degenerate slice for the surface).
- A contract with a missing multiplier and one with a missing currency.
- A stale-option snapshot (option quote older than the age threshold).
- A negative / zero time-to-expiry instance.
- A synthetic known-answer case: prices generated from chosen `sigma` and SVI
  parameters so IV, forward, and the surface fit are analytically recoverable.

The quality gate (`ruff`, `mypy`, `pytest`) runs green on the empty repo, and the
fixture library loads, as a committed acceptance test — not a manual check.

## Invariants you own

Determinism and provenance are designed here (the stamper, the version fields,
numeric-not-string storage). The storage rule "no downstream layer overwrites an
upstream observation" is enforced by your adapters. You do not own the immutable
raw capture itself (that is B) but you give it the schema and the append-only
write path.

## Gotchas

Keep schemas simple and explicit; avoid nested structures unless they clearly cut
complexity. Do not mix time zones in one field. Resist adding fields "just in
case" — every field added after fan-out is a four-way ripple.
