# contracts

The typed objects that cross every workstream boundary, and the one identifier
they all speak. If a value moves between the market-data plane, the analytics
core, the risk engine, or the actor that stamps and stores them, it is one of
these twelve dataclasses, keyed and described here.

## Why this exists

Four other workstreams produce and consume each other's data. Left to themselves
they would each invent their own shape for "an option", their own spelling of a
timestamp, their own loose dict for a snapshot — and the seams between them would
drift until a field meant one thing on the producing side and another on the
consuming side. This module exists so that does not happen. It is the single
vocabulary: one frozen dataclass per table family, one composite key every table
agrees on, and one registry that says, for each table, what its primary key is,
which layer it lives in, whether it is append-only, and which fields must be
provenance-stamped. Validation and the storage codec both read that registry, so
the rules live once and cannot disagree.

The contracts are owned by Workstream A. Nobody outside A edits a definition in
place — a needed change is a request routed to A — because every field added or
moved ripples into four other workstreams at once.

## The public seam

Import from `contracts` (the package), not from its submodules. The seam is
deliberately narrow:

- the twelve table dataclasses (`RawMarketEvent`, `MarketStateSnapshot`,
  `IvPoint`, `PricingResult`, `RiskAggregate`, and so on);
- the three diagnostic bundles (`ForwardDiagnostics`, `IvDiagnostics`,
  `SurfaceFitDiagnostics`) that ride inside derived records;
- `InstrumentKey` plus `OPTION_RIGHTS`, `EVENT_TIMESTAMP_FIELDS`, and
  `broker_contract_id_from_canonical`;
- `validate` / `validate_record` — the write-ahead check;
- `table_for_contract` / `spec_for_table` and the `TableSpec` type, for a caller
  that holds an object and needs its table metadata;
- the error types `ContractError`, `ContractValidationError`, `UnknownTableError`.

The registry's introspection machinery (`REGISTRY`, `resolved_field_types`,
`numeric_field_names`, and friends) is intentionally not re-exported. That is how
the storage codec and the validators are built, not something a consumer should
reassemble against. A consumer that finds itself reaching for it wants a new
method on the seam, routed through A — not the internals.

## The instrument key — the vocabulary the whole system speaks

`InstrumentKey` is the economic identity of one tradable thing. It is a frozen
tuple of nine fields: `underlying_symbol`, `security_type`, `exchange`,
`currency`, `multiplier`, `broker_contract_id`, and — for an option only —
`expiry`, `strike`, `option_right`. Strike and multiplier are real numbers, never
strings. For an underlying (a stock or index) the three option-only fields are
`None`.

`key.canonical()` collapses those nine fields into a single deterministic string,
which is what every table stores and joins on. The format is a fixed pipe-joined
field order; the option-only fields are written as empty slots for an underlying,
so an underlying and its options are the same width and never collide. Two keys
with the same nine fields produce the same string on any machine, in any process
— so the string is built by hand from the fields, never from Python's salted
`hash()`. That determinism is the whole point: it is what lets the same key
resolve identically live and in replay.

```text
underlying|security_type|exchange|currency|multiplier|broker_contract_id|expiry|strike|right
```

For example, an AAPL underlying renders as
`AAPL|STK|SMART|USD|1|u-AAPL||||` (the last three slots empty), while its
$100 June call renders with `2026-06-19|100|C` filling those slots. Floats use a
fixed `.10g` format, so `1.0` is written `1` — meaning the multiplier and strike
do not round-trip losslessly out of the string. The `broker_contract_id` slot is
stored verbatim, so `broker_contract_id_from_canonical(s)` can recover it exactly;
that is what lets replay hand the collector a tick whose contract id resolves
against the instrument universe the same way a live tick would. A string with the
wrong field count is refused with a `ValueError` carrying the offending value,
not silently mis-parsed.

The three event timestamps every raw event carries are named once, in
`EVENT_TIMESTAMP_FIELDS`, so no module invents its own spelling: `exchange_ts`
(when the exchange says it happened), `receipt_ts` (when our process first saw
it), and `canonical_ts` (the single time used for ordering and as-of reads).

## The twelve tables and the registry

Each table is one frozen dataclass, `frozen=True` so it is immutable and compares
by value — which is exactly what makes a write/read round-trip checkable. The
registry (`spec_for_table`) carries one `TableSpec` per table:

| Table | Layer | Primary key | Append-only | Provenance |
|-------|-------|-------------|:-----------:|:----------:|
| `instrument_master` | raw | `(instrument_key, as_of_date)` | yes | no |
| `raw_market_events` | raw | `(session_id, event_id)` | yes | no |
| `market_state_snapshots` | snapshot | `(snapshot_ts, instrument_key)` | no | yes |
| `forward_curve` | derived | `(snapshot_ts, underlying, maturity_years)` | no | yes |
| `iv_points` | derived | `(snapshot_ts, contract_key)` | no | yes |
| `surface_parameters` | derived | `(snapshot_ts, underlying, maturity_years, model_version)` | no | yes |
| `surface_grid` | derived | `(snapshot_ts, underlying, maturity_years, moneyness_bucket)` | no | yes |
| `pricing_results` | derived | `(snapshot_ts, contract_key, pricer_version)` | no | yes |
| `positions` | portfolio | `(valuation_ts, portfolio_id, contract_key)` | no | no |
| `risk_aggregates` | derived | `(valuation_ts, portfolio_id, group_key)` | no | yes |
| `scenario_results` | derived | `(valuation_ts, portfolio_id, scenario_id, contract_key)` | no | yes |
| `qc_results` | qc | `(run_id, check_name, target_key)` | no | no |

The `layer` value is what storage uses to place a partition on disk and to decide
append-only behavior. `requires_provenance` and `requires_source_snapshot_ts`
mark the derived tables that must carry a `ProvenanceStamp` and a
`source_snapshot_ts` back-reference to the snapshot they were computed from.
`positions` is the exception among the keyed-by-valuation tables: it is an input
(a source-of-record or hypothetical position), not a derived value, so it carries
no provenance.

A `TableSpec` also lists `positive_fields` (must be strictly `> 0`, e.g. a
forward, a maturity) and `non_negative_fields` (must be `>= 0`, e.g. a bid, a
gamma, a total variance). Field *types* — which columns are numeric, which are
timestamps, which are nested objects — are not re-listed in the registry; they
are derived from the dataclass type hints, so adding a field to a contract cannot
silently desync a hand-maintained list.

The diagnostic bundles travel inside a derived record as evidence for why a
number came out the way it did — which strikes fed a forward, whether the IV
solver converged, how well an SVI slice fit. They are their own frozen
dataclasses (not loose dicts) so the fields are typed and discoverable, and
storage serializes each as a single JSON column so the tables stay flat.

## Data flow

A producer builds a contract instance and calls `validate(record)` (or
`validate_record(table, record)` when it already knows the table name). The
storage adapter calls the same `validate_record` on every record before a write,
so a malformed record is rejected at the storage door whether or not the producer
checked it first. There is no transformation here — contracts are data, not
behavior. The only computed values are `InstrumentKey.canonical()` and the
registry lookups.

## Validation and failure modes

`validate_record` runs the contract rules in order and raises
`ContractValidationError` on the first failure. The error names the table, the
field, and the offending value, so the rejection log says exactly what was wrong
rather than "validation failed". The checks are:

- every primary-key field is present (not `None`);
- every numeric field is a real finite `int`/`float` — a number arriving as a
  string is an error, not something to quietly `float()`, and `NaN`/`inf` and
  `bool` are rejected;
- `positive_fields` are strictly positive and `non_negative_fields` are
  non-negative;
- every `datetime` field is timezone-aware, never naive;
- a derived record (`requires_source_snapshot_ts`) carries a `source_snapshot_ts`;
- a provenance-bearing record (`requires_provenance`) carries a well-formed
  stamp. The stamp's own wellformedness — tz-aware timestamps, non-empty version
  and config fields, and a hash matching its contents — is checked by delegating
  to `provenance.validate_stamp`; a failure there is surfaced as the
  `ContractValidationError` the write path already expects.

`spec_for_table` and `table_for_contract` raise `UnknownTableError` for a name or
class that is not registered, rather than guessing. None of these are retryable:
a contract violation is a caller bug, and the fix is to correct the record, not
to retry.

## Fastest way to exercise it

```python
from contracts import InstrumentKey, validate
from fixtures.records import baseline_records

# A canonical instrument key round-trips its broker id.
key = InstrumentKey("AAPL", "STK", "SMART", "USD", 1.0, "u-AAPL")
print(key.canonical())

# One valid record per table is in the fixture library; each passes validation.
for table, record in baseline_records().items():
    validate(record)  # raises ContractValidationError if a field is wrong
```

From `backend/`, the contract behavior is pinned by
`tests/test_contract_validation.py` and the storage round-trip in
`tests/test_storage.py`; run them with
`uv run pytest -q tests/test_contract_validation.py`.
