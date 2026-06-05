# universe

Resolve broker option-chain payloads into the canonical instrument universe,
materialize it append-only, and serve the four lookups the rest of the system needs.

## TL;DR

Raw broker rows are loose and string-ish; this turns them strict. Each row is
resolved to a canonical `InstrumentKey` (expiry normalized to one date, strike coerced
numeric, multiplier and currency *required* — never defaulted), duplicates are removed
deterministically, and one append-only `InstrumentMaster` per instrument is written —
keyed point-in-time by `(instrument_key, as_of_date)`, carrying the verbatim broker
payload as evidence. The result is byte-identical across runs and across input order.

## Why this exists

Two layers need a single, trustworthy answer to "what instrument is this?": the
collector resolves an incoming tick's broker contract id to a canonical key before it
persists anything, and the analytics core reads the chain for an underlying. This
module is the one place that answer is computed, so a contract is validated exactly
once — at the seam where untrusted broker data enters — and everyone downstream reads
a clean, canonical universe. It is also where "versioned by date and config" from the
roadmap is realized: the universe is a pure function of the broker chain plus the
`as_of_date`, stored point-in-time.

## Fastest use

```python
from universe import materialize_universe, UniverseService

rows = supervisor.request_option_chain("AAPL")     # raw broker rows
materialize_universe(store, rows, as_of_date)       # append-only, idempotent
u = UniverseService.load_active_universe(store, as_of_date)

u.get_underlying("AAPL")                 # InstrumentKey, or UnknownInstrumentError
u.get_option_chain("AAPL", as_of_date)   # tuple of option keys, or ()
u.resolve_contract("o-AAPL-C-100")       # InstrumentKey, or UnknownContractError
u.symbols()                              # every underlying symbol, sorted
```

## Public interface

The module splits cleanly into a write path, a read path, and the broker-agnostic
chain-selection policy that decides *which* contracts to ask a broker for and *which* of
those to stream.

- `chain_planning.py` — the single chain-selection policy, in broker-neutral terms, in
  two stages bound by one `ChainSelection` config (nearest N expiries, a strike window
  around spot, a minimum per side, and a per-session capture budget). **Discovery:**
  `AvailableChain` is one normalized listing a broker offered, and `plan_chain` composes
  `select_chain` (which listing — primary trading class before a secondary settlement
  class, the SPY/2SPY rule), `select_expiries`, and `select_strikes` into a `ChainPlan` a
  broker adapter expands into real contracts to *qualify into the universe*. **Capture:**
  `select_capture_keys` takes the already-resolved `InstrumentKey`s and returns the
  canonical keys to *actually stream* — the nearest-the-money strikes (both rights) across
  the nearest maturities, capped to `max_strikes_per_session` split across them, underlyings
  always kept. This is the one place the chain is narrowed; there is no second per-broker or
  per-script selection. No broker type appears here — an adapter normalizes its native
  chain-discovery rows into `AvailableChain`, reads back a `ChainPlan`, and the capture stage
  works off the resolved universe.
- `normalization.py` — `resolve_contract_row`: one raw broker row → one validated
  `InstrumentKey`, plus the field normalizers `normalize_expiry` and `normalize_right`.
  This is the strict gate.
- `service.py` — the write path is `resolve_chain` (resolve + dedup + canonical order),
  `build_instrument_masters`, and `materialize_universe` (the idempotent append-only
  write). The read path is `UniverseService` with its four accessors plus `symbols()`.
  `canonical_payload` serializes a broker row to canonical JSON for byte-stable evidence.
- `errors.py` — `UnresolvedContractError` (bad row), `UnknownInstrumentError` /
  `UnknownContractError` (lookup misses), `DuplicateBrokerContractIdError` and
  `InstrumentMasterConflictError` (malformed-chain / immutability violations).

## Data flow

```
broker chain rows (loose dicts)
   │  resolve_contract_row   ← strict validation, the trust gate
   ▼
ResolvedContract (InstrumentKey + verbatim payload)
   │  resolve_chain          ← dedup to canonical key, sort by key
   ▼
build_instrument_masters     ← one (instrument_key, as_of_date) row each
   │  materialize_universe    ← idempotent append-only write to storage
   ▼
instrument_master table  ──load_active_universe──►  UniverseService (the four lookups)
```

The diagram omits the conflict and duplicate checks (covered under failure modes) and
the in-memory construction path: a `UniverseService` can also be built directly from a
freshly resolved chain without a round-trip through storage.

## State and lifecycle

The write path is stateless; the only state is what lands in the append-only
`instrument_master` table. `UniverseService` is built once for an `as_of_date` and is
read-only thereafter — it indexes underlyings and option chains by symbol and every
instrument by its broker contract id. A `get_option_chain` for a date other than the
one the service was built for returns empty by design, so the service answers honestly
for exactly its own date and no other.

## The trust boundary

This is where untrusted vendor data is made trustworthy. The connectivity layer
guarantees a tick's *shape* but not its *content*; here every field is validated or
the row is rejected. A missing multiplier or currency, an unparseable expiry, a
non-numeric or non-positive strike, a bad option right, or a missing `conId` all raise
`UnresolvedContractError` carrying the verbatim payload and the offending field — and a
single bad row fails the whole `resolve_chain` rather than being silently dropped, so
a malformed chain is loud, not lossy. Booleans are explicitly rejected as numbers
(`True` is not the strike 1.0). What this module owns is the canonical identity of
every instrument; what it delegates down is the persistence (A's append-only store)
and up is the choice of *which* underlyings to discover (`PlatformConfig.universe`).

## Configuration

The universe content is a pure function of the broker chain it is given plus the
`as_of_date`. *Which* underlyings to discover comes from `PlatformConfig.universe`
(economics, owned by A and passed in by the orchestration), so a config change yields
a different instrument set — the "versioned by date and config" the spec asks for, with
the date as the storage key and the config determining the content. Two broker expiry
formats are accepted and fold to one canonical date: IBKR's compact `YYYYMMDD` and ISO
`YYYY-MM-DD`. Option rights `C`/`P`/`CALL`/`PUT` in any case fold to `C` or `P`.

## Failure modes

- **Unresolvable row** → `UnresolvedContractError`, carrying payload and field. Not
  retryable; the chain is malformed. Fails the whole `resolve_chain`.
- **Duplicate broker contract id** → `DuplicateBrokerContractIdError` at
  `UniverseService` construction, naming both colliding keys, when two *distinct*
  instruments share one `conId` (a broker reused the id). Never a last-write-wins
  overwrite.
- **Changed evidence for an existing key** → `InstrumentMasterConflictError` from
  `materialize_universe`, carrying both payloads. The raw layer is immutable: the first
  write for an `(instrument_key, as_of_date)` stands; an exact re-run is a silent no-op;
  genuinely new evidence belongs under a new `as_of_date`.
- **Lookup miss** is split deliberately. An unknown underlying (`get_underlying`) or an
  unknown contract id (`resolve_contract`) raises `UnknownInstrumentError` /
  `UnknownContractError` with diagnostics — those are real "you asked for something that
  isn't here" failures. But a `get_option_chain` miss (no options, or wrong date)
  returns an empty tuple — a legitimate "nothing here", not an error.

## Fastest way to exercise it

`backend/tests/test_universe.py` drives every path with in-memory chains and a
`tmp_path` store: run `cd backend && uv run pytest -q tests/test_universe.py`. The
determinism guarantees (byte-identical across runs, invariant to broker row order,
identical digest across processes) and the immutability conflict are all pinned there.
