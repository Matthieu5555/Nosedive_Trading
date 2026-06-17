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
  `enabled` switch. `parse_index_registry` validates every entry through the same
  pydantic v2 strict/extra-forbid seam as the core config sections (M16) — a bad field
  raises a labeled `IndexRegistryError` (symbol/field/value/reason) — and **rejects an
  unknown calendar code rather than defaulting it** (a wrong calendar = a wrong close
  instant = a look-ahead bug). The provider-agnostic fields describe the index; only `ibkr:` is
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

> The two seed entries (SX5E on `XEUR`/EUREX, SPX on `XNYS`/CBOE) now ship **`enabled: true`**
> so the live EOD spine surfaces them through `enabled_indices()` and exercises the
> calendar → close-capture → `project_grid` → persist path (which uses only the calendar code,
> currency, and symbol, never the conid). Their IBKR conids remain **unverified placeholders
> (`0`)**: the conid is consumed only by the 1C broker→raw-event qualification seam, which is not
> yet closed, so a placeholder cannot mis-resolve a live contract here. Before that seam qualifies
> these indices, replace `conid: 0` with the real verified IBKR contract id (see the `TODO(1C)`
> in `configs/universe.yaml`) — a wrong conid would silently qualify the wrong contract, which is
> why it is left at 0 rather than guessed.

## Chain selection

Two coexisting strike-selection policies sit over the *same* listed-strike shape — one
policy surface, not one per broker or per script:

- **%-of-spot** (`select_strikes`, the `ChainSelection` defaults) — keep strikes inside
  `spot ± strike_window_pct` with a per-side floor. `plan_chain`
  turns an `AvailableChain` into the concrete `(expiry, strike)` capture keys and
  `select_capture_keys` is the key-level entry point.

The **expiry axis** is selected separately from the strike axis. When the selection is
**tenor-targeted** (`tenor_years` + `as_of` set, `targets_tenors`) expiries bracket the
pinned tenor grid (`select_expiries_bracketing`). Otherwise `max_expiries` governs:
an integer keeps the nearest *N* by date, and **`None` keeps every listed maturity**
(`select_expiries`/`_nearest_expiries` short-circuit to the full sorted set). The EOD
capture default (`_selection_from_config`) is `max_expiries=None` — the full term
structure, every listed expiry out to the longest LEAP, per the owner ruling that the
clean-fetch contract is the whole chain. Nearest-*N* truncation is **not** used for the
capture long end: a finite budget front-loads weeklies and silently drops the LEAPs once a
chain lists more expiries than the budget (the latent SPX / weekly-heavy-SX5E regression
`38910d9` re-armed and this change removes).
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

`select_discovery_strikes` (T-delta-window) is the **discovery** sibling of the delta band:
it qualifies the listed strikes a broker capture must resolve so the economic 30Δ selection
above can later reach the true band. It reuses `select_strikes_delta_band` — the same single
delta source, the pricing engine — at a *looser* bound (`discovery_delta_bound`, the economic
bound minus a ~20Δ margin) and a conservative `discovery_working_vol` seed (the fitted vol is
only known downstream), so the qualified set is a guaranteed superset of the 30Δ band that
scales with √T. It replaced a fixed strike count that clipped the band's long end to ~ATM±1%;
it has no cap (a cap would re-create that clip), the broker's listed strikes being the natural
bound.

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

**Membership sources** (`membership_source.py`) parse a raw vendor feed into typed
`MembershipChange` rows — never touching storage. `SP500DatasetsSource` (SPX, dated, no
weights) and `YfiuaSnapshotSource` (SX5E, current snapshot, no weights) pull free feeds;
`CsvFileSource` reads a committed local CSV with an optional `Weight` column. **Current
state (MVP):** SPX + SX5E weights are seeded from **SSGA SPDR ETF holdings** (SPY/FEZ) via
`CsvFileSource` (`configs/index_weights/`, ingested by `scripts/ingest_membership.py --csv`),
the honest free proxy for index weights until the OQ-3 source (Siblis Research) lands. A
blank weight cell stays `None`, never zeroed.

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

### Top-N by weight — the S1 dispersion selector (ADR 0044)

`top_n_by_weight(store, index, as_of_date, n, *, known_as_of=None)` returns the `n` heaviest
constituents by index weight, **point-in-time**: it resolves the as-of basket through `members`
(adding only a rank on top — not a second resolver, so look-ahead stays policed in one place) and
sorts **descending weight, ties broken by ascending constituent symbol** (deterministic across
storage/ingest order). `n` is the selection size the caller sources from config —
`UniverseConfig.dispersion_top_n` (default 10 = course top-10; `configs/universe.yaml` sets 50 =
theory top-50) — passed in as a parameter so the selector stays a pure injected function.

Two refusals, both labeled `MembershipRankingError`, never a silent wrong answer: a non-positive
`n`, and a basket carrying **any** labeled-unavailable (`None`) weight — you cannot rank what
isn't known, and dropping/zeroing the unweighted names would bias the selection. An *empty* basket
(unknown index or pre-history date) is not an error: it returns `()`. A basket smaller than `n`
returns all of it (a smaller live index is legitimate, never padded). Weights are ranked as raw
magnitudes, so the SSGA percent feed (summing ≈ 96, not 1.0) ranks identically to a fractional
source — ranking needs only the relative order.

```python
from datetime import date
from algotrading.core.config import load_platform_config
from algotrading.infra.universe import top_n_by_weight

n = load_platform_config("configs").universe.dispersion_top_n   # typed, hashed (50)
basket = top_n_by_weight(store, "SX5E", date(2026, 6, 9), n)    # heaviest-first, deterministic
names = [member.constituent for member in basket]               # the S1 names to capture/trade
```

Tests: `packages/infra/tests/test_membership.py` — as-of basket correctness, both interval
boundaries, the today's-list-is-not-history negative guard, weights-as-of, bitemporal
restatement, contract round-trip + malformed-rejection seam, the edge-case floor,
reordering invariance, and SP500 on the same contract/resolver.
`packages/infra/tests/test_membership_ranking.py` — `top_n_by_weight`: descending-weight order
and the ascending-symbol tie-break (hand-derived), every N slice, N-larger-than-basket returns
all, ingest-order invariance, the point-in-time guard (a name added later is excluded from a past
date's top-N) and the knowledge-axis vintage, the two labeled refusals (non-positive N; any
unavailable weight), empty/pre-history baskets return `()`, and the shipped SSGA SX5E CSV ranked
end to end (`CsvFileSource` → ingest → top-N) against a temp store.

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

