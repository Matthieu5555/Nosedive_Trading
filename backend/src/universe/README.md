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

## Fastest use

```python
from universe import materialize_universe, UniverseService

rows = supervisor.request_option_chain("AAPL")     # raw broker rows
materialize_universe(store, rows, as_of_date)       # append-only, idempotent
u = UniverseService.load_active_universe(store, as_of_date)

u.get_underlying("AAPL")                 # InstrumentKey, or UnknownInstrumentError
u.get_option_chain("AAPL", as_of_date)   # tuple of option keys, or ()
u.resolve_contract("o-AAPL-C-100")       # InstrumentKey, or UnknownContractError
```

## What's here

- `normalization.py` — `resolve_contract_row`: one raw row → one validated
  `InstrumentKey`, plus the field normalizers (`normalize_expiry`, `normalize_right`).
- `service.py` — `resolve_chain` (resolve + dedup + canonical order),
  `build_instrument_masters`, `materialize_universe` (idempotent append-only write),
  and `UniverseService` with the four accessors.
- `errors.py` — `UnresolvedContractError` (bad row, carries the payload and field),
  `UnknownInstrumentError` and `UnknownContractError` (lookup misses).

## Behaviour worth knowing

- **Rejected, not defaulted, not skipped.** A missing multiplier or currency, an
  unparseable expiry, a non-numeric strike, or a bad option right raises
  `UnresolvedContractError` carrying the verbatim payload and the offending field. A
  single bad row fails the whole `resolve_chain` rather than being silently dropped.
- **Deterministic dedup.** Rows resolving to the same canonical key collapse to one,
  keeping the contract whose canonical evidence payload sorts first; output is ordered
  by canonical key. Two different broker date formats (`20260619`, `2026-06-19`) for
  the same contract normalize to one key and deduplicate.
- **Idempotent materialization, loud on conflict.** Re-running for the same date with
  identical evidence writes nothing new (the raw layer is immutable, so the first write
  for an instrument/date stands). Re-running with a *changed* payload for an existing
  `(instrument_key, as_of_date)` raises `InstrumentMasterConflictError` carrying both
  payloads — it never silently returns the new evidence while leaving the old on disk.
  Genuinely new evidence for an instrument is a new row under a new `as_of_date`.
- **The broker contract id is an external foreign key.** `resolve_contract` looks up
  by it, but it is only one of the nine `InstrumentKey` fields, never the platform's
  sole identifier. It must be unique within a universe: two distinct instruments
  sharing one id is a malformed chain and raises `DuplicateBrokerContractIdError` at
  construction, rather than silently keeping the last in a last-write-wins overwrite.

## Configuration

The universe content is a pure function of the broker chain it is given plus the
`as_of_date`. *Which* underlyings to discover comes from `PlatformConfig.universe`
(economics, owned by A and passed in by the orchestration), so a config change yields
a different instrument set — the "versioned by date and config" the spec asks for, with
the date as the storage key and the config determining the content.
