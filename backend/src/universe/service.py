"""Resolve a broker chain into the canonical universe, materialize it, and serve it.

The pipeline: resolve every raw row to an instrument key (rejecting bad ones),
deduplicate deterministically, sort into a canonical order, and materialize one
append-only :class:`~contracts.InstrumentMaster` per instrument — each carrying the
verbatim broker payload as evidence for how it was resolved. Determinism is the load-
bearing property: the same chain resolved twice yields a byte-identical set of
masters, and the input order of the broker rows never changes the output, because the
output is sorted by canonical instrument key and the evidence payload is canonical
JSON.

:class:`UniverseService` is the read side — the four accessors the rest of the system
uses. It can be built in memory from a freshly resolved chain or loaded from storage
for a session date.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from contracts import InstrumentKey, InstrumentMaster
from storage import ParquetStore

from .errors import (
    DuplicateBrokerContractIdError,
    InstrumentMasterConflictError,
    UnknownContractError,
    UnknownInstrumentError,
)
from .normalization import resolve_contract_row

_INSTRUMENT_MASTER = "instrument_master"


def canonical_payload(payload: Mapping[str, object]) -> str:
    """Serialize a broker payload to canonical JSON: sorted keys, stable separators.

    The values are preserved verbatim; only the key order is canonicalized, which is
    what makes the stored evidence byte-identical across runs regardless of how the
    broker happened to order the row. ``default=str`` keeps a stray date or Decimal
    serializable rather than crashing the materialization.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


@dataclass(frozen=True, slots=True)
class ResolvedContract:
    """One resolved instrument plus the verbatim broker row that produced it."""

    instrument: InstrumentKey
    raw_payload: Mapping[str, object]


def resolve_chain(rows: Sequence[Mapping[str, object]]) -> tuple[ResolvedContract, ...]:
    """Resolve raw broker rows to instruments, deduplicated and in canonical order.

    Every row is resolved (a bad one raises :class:`UnresolvedContractError`, never a
    silent skip). Duplicates — rows resolving to the same canonical instrument key —
    are collapsed to one, keeping the contract whose canonical evidence payload sorts
    first, so the choice is deterministic even when two duplicate rows differ in a
    non-economic field. The result is ordered by canonical key.
    """
    deduplicated: dict[str, ResolvedContract] = {}
    for row in rows:
        instrument = resolve_contract_row(row)
        key = instrument.canonical()
        candidate = ResolvedContract(instrument=instrument, raw_payload=dict(row))
        incumbent = deduplicated.get(key)
        if incumbent is None or canonical_payload(candidate.raw_payload) < canonical_payload(
            incumbent.raw_payload
        ):
            deduplicated[key] = candidate
    return tuple(deduplicated[key] for key in sorted(deduplicated))


def build_instrument_masters(
    resolved: Sequence[ResolvedContract], as_of_date: date
) -> tuple[InstrumentMaster, ...]:
    """Build the canonical, append-only master rows for a resolved universe.

    One row per instrument, keyed point-in-time by ``(instrument_key, as_of_date)``,
    each keeping its verbatim broker payload as evidence. Sorted by instrument key so
    the materialized set is byte-identical across runs.
    """
    masters = [
        InstrumentMaster(
            instrument_key=contract.instrument.canonical(),
            as_of_date=as_of_date,
            instrument=contract.instrument,
            raw_broker_payload=canonical_payload(contract.raw_payload),
        )
        for contract in resolved
    ]
    return tuple(sorted(masters, key=lambda master: master.instrument_key))


def materialize_universe(
    store: ParquetStore, rows: Sequence[Mapping[str, object]], as_of_date: date
) -> tuple[InstrumentMaster, ...]:
    """Resolve a chain and write its masters to the append-only raw layer, idempotently.

    Returns the full canonical set of masters for the date. Re-running with the *same*
    chain writes nothing new: any ``(instrument_key, as_of_date)`` already on disk with
    identical evidence is skipped, so a repeated materialization is a no-op rather than
    an append-only collision. Re-running with *changed* evidence for an existing key —
    a different verbatim broker payload for the same instrument and date — raises
    :class:`InstrumentMasterConflictError` instead of silently returning the new payload
    while leaving the old one on disk. Because the raw layer is immutable, the first
    materialization for a given instrument and date is the one that stands.
    """
    resolved = resolve_chain(rows)
    masters = build_instrument_masters(resolved, as_of_date)
    stored = {
        (master.instrument_key, master.as_of_date): master
        for master in store.read(_INSTRUMENT_MASTER)
    }
    fresh: list[InstrumentMaster] = []
    for master in masters:
        incumbent = stored.get((master.instrument_key, master.as_of_date))
        if incumbent is None:
            fresh.append(master)
        elif incumbent != master:
            raise InstrumentMasterConflictError(
                instrument_key=master.instrument_key,
                as_of_date=master.as_of_date,
                stored_payload=incumbent.raw_broker_payload,
                incoming_payload=master.raw_broker_payload,
            )
    if fresh:
        store.write(_INSTRUMENT_MASTER, fresh)
    return masters


class UniverseService:
    """The read side of the universe: the four accessors over a resolved instrument set.

    Built from a set of canonical instrument keys for one ``as_of_date`` — either a
    freshly resolved chain or the masters loaded from storage. Underlyings and option
    chains are indexed by symbol; every instrument is indexed by its broker contract
    id (the external foreign key) for :meth:`resolve_contract`.
    """

    def __init__(self, instruments: Sequence[InstrumentKey], as_of_date: date) -> None:
        self._as_of_date = as_of_date
        self._underlyings: dict[str, InstrumentKey] = {}
        chains: dict[str, list[InstrumentKey]] = defaultdict(list)
        self._by_broker_id: dict[str, InstrumentKey] = {}
        for instrument in instruments:
            incumbent = self._by_broker_id.get(instrument.broker_contract_id)
            if incumbent is not None and incumbent != instrument:
                raise DuplicateBrokerContractIdError(
                    instrument.broker_contract_id, incumbent.canonical(), instrument.canonical()
                )
            self._by_broker_id[instrument.broker_contract_id] = instrument
            if instrument.is_option():
                chains[instrument.underlying_symbol].append(instrument)
            else:
                self._underlyings[instrument.underlying_symbol] = instrument
        self._chains: dict[str, tuple[InstrumentKey, ...]] = {
            symbol: tuple(sorted(options, key=lambda option: option.canonical()))
            for symbol, options in chains.items()
        }

    @property
    def as_of_date(self) -> date:
        return self._as_of_date

    def symbols(self) -> tuple[str, ...]:
        """Every underlying symbol in the universe, sorted."""
        return tuple(sorted(self._underlyings))

    def get_underlying(self, symbol: str) -> InstrumentKey:
        """Return the underlying instrument for a symbol, or raise if unknown."""
        try:
            return self._underlyings[symbol]
        except KeyError:
            raise UnknownInstrumentError(symbol, tuple(sorted(self._underlyings))) from None

    def get_option_chain(self, symbol: str, as_of_date: date) -> tuple[InstrumentKey, ...]:
        """Return the option contracts for a symbol active on a date, in canonical order.

        Empty when the symbol has no options or the date is not the one this universe
        was resolved for — a legitimate "nothing here", distinct from the loud failure
        of looking up an unknown underlying or an unresolved contract id.
        """
        if as_of_date != self._as_of_date:
            return ()
        return self._chains.get(symbol, ())

    def resolve_contract(self, broker_contract_id: str) -> InstrumentKey:
        """Resolve a broker contract id (external FK) to its canonical instrument key."""
        try:
            return self._by_broker_id[broker_contract_id]
        except KeyError:
            raise UnknownContractError(broker_contract_id, len(self._by_broker_id)) from None

    @classmethod
    def load_active_universe(cls, store: ParquetStore, session_date: date) -> UniverseService:
        """Load the universe materialized for a session date back from storage.

        Reads the instrument masters whose ``as_of_date`` is the session date and
        rebuilds the service from their stored instrument keys. A date with nothing
        materialized yields an empty universe, whose accessors then miss as usual.
        """
        instruments = tuple(
            master.instrument
            for master in store.read(_INSTRUMENT_MASTER)
            if master.as_of_date == session_date
        )
        return cls(instruments, session_date)
