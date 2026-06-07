# universe — resolve broker chains, materialize masters, serve lookups

TL;DR: turn a broker's raw option-chain rows into canonical instrument masters, plan
which strikes/expiries to capture, and serve as-of lookups over the resolved universe.
This is the reference-data layer the capture and analytics paths key off.

```python
from datetime import date
from algotrading.infra.storage import ParquetStore
from algotrading.infra.universe import (
    materialize_universe, UniverseService, ChainSelection, select_capture_keys,
)

store = ParquetStore("<data-root>")
# resolve broker rows → canonical InstrumentMaster rows, written append-only (idempotent
# on (instrument_key, as_of_date); conflicting evidence raises, never silently overwrites)
masters = materialize_universe(store, broker_rows, date(2026, 6, 1))
universe = UniverseService.load_active_universe(store, date(2026, 6, 1))  # read side
universe.symbols(); universe.get_option_chain("AAPL", date(2026, 6, 1))   # four accessors

# which (expiry, strike) keys to capture, from a %-of-spot window around each spot
keys = select_capture_keys(instruments, spots={"AAPL": 100.0}, selection=ChainSelection())
```

## Two instrument models, both kept by design (ADR 0023)

This package carries two models that coexist deliberately — this is not transitional:

- **The analytics-facing universe** — `chain_planning` (the one selection policy:
  `ChainSelection` → `plan_chain` / `select_capture_keys` over `AvailableChain`),
  `service` (`resolve_chain` → `build_instrument_masters` → `materialize_universe`, and
  the read-side `UniverseService`), and `normalization` (`resolve_contract_row`,
  `normalize_expiry`, `normalize_right`).
- **The vendored M5 instrument model** — `contracts.py` / `discovery.py`:
  `Underlying`, `OptionContract`, the reversible `instrument_key` /
  `parse_instrument_key`, `OptionParams`, `normalize_option_params`. ADR 0023 keeps
  Vincent's Saxo/Deribit adapters as survivors, and those broker leaves import this
  model, so it is a **permanent** re-export from this package, not a slice awaiting
  removal.

Errors live in `errors.py` (`UniverseError` base + `UnresolvedContractError`,
`UnknownInstrumentError`, `UnknownContractError`, `DuplicateBrokerContractIdError`,
`InstrumentMasterConflictError`, `IndexRegistryError`, `CalendarResolutionError`). All
public names are re-exported from the package root — import from
`algotrading.infra.universe`, never from a submodule.

## Index registry + calendar resolver (ADR 0035)

A third concern lives here: *which indices the platform tracks*, and *when each one
closes* — distinct from the membership of an index (1A) and from the instrument masters
above. Two pieces:

- **`index_registry`** — the typed `IndexRegistry` over the `indices:` block in
  `configs/universe.yaml`. Each `IndexEntry` carries `symbol` (the registry key, shared
  with 1A/1C/1G/1I), `name`, an `exchange_calendars` `calendar` code, a 3-letter
  `currency`, an `IbkrRef` provider sub-block (`conid`/`secType`/`exchange`), and an
  `enabled` switch. `parse_index_registry` validates every entry and **rejects an unknown
  calendar code rather than defaulting it** (a wrong calendar = a wrong close instant = a
  look-ahead bug). The provider-agnostic fields describe the index; only `ibkr:` is
  IBKR-specific, so a future `saxo:`/`deribit:` sibling joins under the same key (ADR 0023).
  The block stays inside the hashed `universe` bundle (`config_hashes["universe"]`) with
  **no separate hash** — the typed object is *not* hashed; the raw block on
  `UniverseConfig.indices` is what enters the hash (the calendar library lives in infra, so
  core stays blind to it).
- **`calendar_resolver`** — `CalendarResolver(registry)` is the thin port over
  `exchange_calendars`. `is_session(index, on_date)` and `session_close(index, on_date)`
  (a **tz-aware UTC** `datetime`) are the two answers 1C/1G consume. The date is always
  **injected — the resolver reads no wall clock** (1C's byte-identical replay and 1G's
  idempotent ledger depend on it). Holidays, half-days, and DST are the library's job;
  out-of-coverage dates and non-session closes raise a labeled `CalendarResolutionError`.

The single seam downstream tasks read is **`enabled_indices(registry)`** (1A/1C/1G/1I) —
load the registry once via `load_index_registry(configs_dir)` or
`index_registry_from_config(platform_config)`; never re-parse the YAML.

```python
from datetime import date
from algotrading.infra.universe import (
    load_index_registry, enabled_indices, CalendarResolver,
)

registry = load_index_registry("configs")
for entry in enabled_indices(registry):           # only the enabled set reaches capture
    resolver = CalendarResolver(registry)
    if resolver.is_session(entry.symbol, date(2026, 6, 8)):
        close = resolver.session_close(entry.symbol, date(2026, 6, 8))  # tz-aware UTC
```

> The two seed entries (SX5E on `XEUR`/EUREX, SPX on `XNYS`/CBOE) ship **`enabled: false`**:
> their IBKR conids are unverified placeholders and equity/index capture at scale is gated on
> D1's `provider` partition segment (ADR 0034 §4). Verify a conid and land the capture path
> before flipping one to `enabled: true`.

## Chain selection

Two coexisting strike-selection policies sit over the *same* listed-strike shape — one
policy surface, not one per broker or per script:

- **%-of-spot** (`select_strikes`, the `ChainSelection` defaults) — keep strikes inside
  `spot ± strike_window_pct` with a per-side floor, under a max-expiries cap. `plan_chain`
  turns an `AvailableChain` into the concrete `(expiry, strike)` capture keys and
  `select_capture_keys` is the key-level entry point. The window is a request-shaping
  heuristic, so it lives in code.
- **delta band** (`select_strikes_delta_band`, WS 1B) — keep, **per tenor**, the contiguous
  block of listed strikes from the 30Δ put through ATM to the 30Δ call (the whole central
  smile, not three pillars). It runs per expiry because the same dollar strike is a
  different delta at each maturity/forward. Delta is read from the pricing engine at
  `carry == 0` (`pricing.from_forward(spot=None)`, so spot delta and forward delta
  coincide), never re-derived here. When too few strikes fall inside the band on a side (a
  thin or all-wing listing), a per-side nearest-the-money floor still returns a fittable
  slice, labeled, never an empty silent result. Unusable pricing inputs (zero/non-finite
  forward, vol, maturity, or an out-of-range discount factor) raise a labeled
  `StrikeSelectionError`, never a bare `NaN` strike.

```python
from algotrading.core.config import load_platform_config
from algotrading.infra.universe import select_strikes_delta_band

selection = load_platform_config("configs").universe.strike_selection   # typed, hashed
band = select_strikes_delta_band(
    listed_strikes, forward=F, maturity_years=T, discount_factor=DF,
    volatility=working_vol, selection=selection,
)  # the contiguous [30Δ put, 30Δ call] block at this tenor
```

The 30Δ bound and the **delta convention** are economic — they decide which strikes the
captured chain holds — so they come from typed `universe.yaml` config
(`StrikeSelectionConfig`, ADR 0028 / C7 audited site), never a `.py` literal, and travel
inside `config_hashes["universe"]`. The convention flag (`forward_undiscounted` vs
`spot_discounted`) pins whether the bound is read against the undiscounted forward delta
`N(d1)` or the engine's discounted spot delta `DF·N(d1)` (they differ by the discount
factor, which can move the boundary strike). A bad convention value raises
`ConfigFieldError`, never a silent default.

## Point-in-time index membership (WS 1A)

`members(store, index, as_of_date)` resolves an index's basket **exactly as it stood on
`as_of_date`**, with that date's weights, through a DuckDB `ASOF JOIN` over the Parquet
store (ADR 0033) — the one resolver 1C capture and 1I's constituent list both consume, so
the look-ahead audit polices a single surface, not a join per consumer. There is no path
that reads "current" membership for a past date.

The data is the bitemporal `IndexConstituent` contract (`contracts.tables`), stored
append-only in a `reference` layer partitioned by index then `effective_add_date` (ADR
0034 §4/§5). It is **provider-agnostic** — reference data describes the index, not a quote
source, so it carries no `provider=` partition segment; the data source is the `vendor`
*field* (OQ-3: Siblis Research). Two time axes:

- **effective** — the half-open interval `[effective_add_date, effective_remove_date)` the
  name was a member; `effective_remove_date` is `None` for a current member. A name is in
  the basket *on* its add date and *out* on its remove date.
- **knowledge** — `knowledge_date`, when the fact was recorded. A vendor restatement of past
  membership writes a **new row** under a later `knowledge_date`, never an in-place edit
  (ADR 0019/0034 immutability). `members(..., known_as_of=K)` answers "what did we believe
  on K" by filtering to `knowledge_date <= K` before the effective join.

`weight` is **nullable**: where the source lacks full weights it is `None` (labeled
unavailable), never zeroed or equal-weighted. `basket_weight_sum(basket)` returns `None`
(not `0.0`) when any weight is unavailable, so a partial basket is never treated as complete.

```python
from datetime import date
from algotrading.infra.storage import ParquetStore
from algotrading.infra.universe import (
    MembershipChange, ingest_membership_changes, members, basket_weight_sum,
)

store = ParquetStore("<data-root>")
# raw-source parsing stays separate from the typed contract: a vendor reader builds
# MembershipChange rows; a second vendor lands on the same contract + resolver.
ingest_membership_changes(store, [
    MembershipChange("SX5E", "ASML", date(2010, 1, 1), None, date(2010, 1, 1), "Siblis", 0.10),
])
basket = members(store, "SX5E", date(2021, 1, 15))            # as-of basket, sorted by name
# a declared full-weighted snapshot is checked to sum near 1.0 on write:
# ingest_membership_changes(store, snapshot_rows, complete_snapshot=True)
```

> **As-of contract.** `as_of_date` is the date to reconstruct *as of* — pass the date being
> analyzed or replayed, **never** `date.today()` for a historical computation. The resolver
> reads no wall clock. `check-lookahead-bias` over `membership.py` and its callers passes
> with zero findings; keep it that way.

Tests: `packages/infra/tests/test_membership.py` — as-of basket correctness, both interval
boundaries, the today's-list-is-not-history negative guard, weights-as-of, bitemporal
restatement, contract round-trip + malformed-rejection seam, the edge-case floor,
reordering invariance, and SP500 on the same contract/resolver.

The earlier generic `(instrument_key, as_of_date)` key (masters, chain resolution) is the
foundation 1A builds on; it stays for the instrument-level lookups above.

## Test coverage

The index registry and calendar resolver have standalone suites:
`packages/infra/tests/test_index_registry.py` (round-trip, per-entry validation incl. the
unknown-calendar-code rejection, the enabled filter, the universe-bundle hash discipline)
and `packages/infra/tests/test_calendar_resolver.py` (session vs holiday per exchange,
tz-correct close at different UTC instants, the half-day early close, labeled failures, and
the no-wall-clock proof). Expected calendar facts are hand-encoded from the published
exchange calendars, never read back from the resolver.

The **delta-band** policy (WS 1B) has a direct unit suite,
`packages/infra/tests/test_chain_planning.py`: the band spans exactly the listed
`[30Δ put, 30Δ call]` block (boundaries derived independently via a scipy `norm.ppf`
oracle, distinct from the engine's `math.erf` path), count varies with listing density (the
1B acceptance), selection differs by tenor, ATM is always inside and the 10Δ wings are out,
the 30Δ-exact strike is kept (boundary-inclusive), the convention flag moves the boundary
and a bad flag raises `ConfigFieldError`, the shipped `universe.yaml` builds the band through
the typed `from_config` path, plus the TESTING.md edge-case floor (empty/single/all-wing,
reordering invariance, and labeled `StrikeSelectionError` on every unusable pricing input).
Named ladder fixtures live in the shared library (`fixtures.synthetic.build_delta_band_ladder`
/ `delta_band_boundary_strike`).

The **%-of-spot** `select_strikes`/`plan_chain` policy and the broker-chain resolver are
still exercised **through** the collection and orchestration paths
(`packages/infra/tests/test_collection_use_cases.py`, `test_orchestration.py`,
`test_handover_e2e.py`) and the per-broker discovery suites
(`packages/infra-{ibkr,saxo,deribit}/tests/test_*_discovery.py`); a direct unit test of the
%-of-spot keys against hand-derived expectations remains a smaller open gap.

