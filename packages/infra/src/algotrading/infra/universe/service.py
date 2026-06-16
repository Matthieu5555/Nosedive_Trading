from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from algotrading.infra.contracts import InstrumentKey, InstrumentMaster
from algotrading.infra.storage import ParquetStore

from .errors import (
    DuplicateBrokerContractIdError,
    InstrumentMasterConflictError,
    UnknownContractError,
    UnknownInstrumentError,
)
from .normalization import resolve_contract_row

_INSTRUMENT_MASTER = "instrument_master"


def canonical_payload(payload: Mapping[str, object]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


@dataclass(frozen=True, slots=True)
class ResolvedContract:

    instrument: InstrumentKey
    raw_payload: Mapping[str, object]


def resolve_chain(rows: Sequence[Mapping[str, object]]) -> tuple[ResolvedContract, ...]:
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
        return tuple(sorted(self._underlyings))

    def get_underlying(self, symbol: str) -> InstrumentKey:
        try:
            return self._underlyings[symbol]
        except KeyError:
            raise UnknownInstrumentError(symbol, tuple(sorted(self._underlyings))) from None

    def get_option_chain(self, symbol: str, as_of_date: date) -> tuple[InstrumentKey, ...]:
        if as_of_date != self._as_of_date:
            return ()
        return self._chains.get(symbol, ())

    def resolve_contract(self, broker_contract_id: str) -> InstrumentKey:
        try:
            return self._by_broker_id[broker_contract_id]
        except KeyError:
            raise UnknownContractError(broker_contract_id, len(self._by_broker_id)) from None

    @classmethod
    def load_active_universe(cls, store: ParquetStore, session_date: date) -> UniverseService:
        instruments = tuple(
            master.instrument
            for master in store.read(_INSTRUMENT_MASTER)
            if master.as_of_date == session_date
        )
        return cls(instruments, session_date)
