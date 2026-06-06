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
`InstrumentMasterConflictError`). All public names are re-exported from the package
root — import from `algotrading.infra.universe`, never from a submodule.

## Chain selection

`ChainSelection` is the one selection policy. Today it selects by a %-of-spot strike
window (`strike_window_pct`) and a max-expiries cap; `plan_chain` turns an
`AvailableChain` into the concrete `(expiry, strike)` capture keys, and
`select_capture_keys` is the key-level entry point. The medium-term roadmap adds a
**delta-band** selection variant beside the %-of-spot one (roadmap WS 1B) — it slots in
here as another policy over the same `AvailableChain`, not a parallel module.

## As-of discipline

Index→constituent membership and chain resolution are reference data keyed by
`(instrument_key, as_of_date)`. Historical joins must resolve membership as of the
date being reconstructed, never today's list — see the `check-lookahead-bias` skill.
Point-in-time membership (roadmap WS 1A) builds on this key.

## Test coverage

There is no standalone `test_universe.py` today: the selection policy and resolver are
exercised **through** the collection and orchestration paths
(`packages/infra/tests/test_collection_use_cases.py`, `test_orchestration.py`,
`test_handover_e2e.py`) and the per-broker discovery suites
(`packages/infra-{ibkr,saxo,deribit}/tests/test_*_discovery.py`). A direct unit test of
`ChainSelection`/`plan_chain` against hand-derived expected keys is a known coverage gap
worth closing before the delta-band variant (roadmap WS 1B) lands on top of it.

