"""Errors raised while resolving and serving the instrument universe.

Every one carries enough to diagnose the failure without re-running: the resolution
errors keep the raw broker payload and the field that failed, and the lookup errors
keep the key that missed and what was available. The whole point, per the spec, is
that an unresolved contract is *surfaced loudly with diagnostics, never silently
skipped*.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date


class UniverseError(Exception):
    """Base class for all universe-layer failures."""


class UnresolvedContractError(UniverseError):
    """A raw broker contract row could not be turned into a valid instrument.

    Carries the verbatim payload, the offending field, and a plain-language reason, so
    a rejected contract names exactly what was wrong (a missing multiplier, an
    unparseable expiry) instead of being dropped from the universe unnoticed.
    """

    def __init__(self, payload: Mapping[str, object], field: str, reason: str) -> None:
        self.payload = dict(payload)
        self.field = field
        self.reason = reason
        super().__init__(f"unresolved contract: {field}: {reason}; payload={self.payload!r}")


class UnknownInstrumentError(UniverseError):
    """A symbol was looked up that has no underlying in the universe."""

    def __init__(self, symbol: str, known: tuple[str, ...]) -> None:
        self.symbol = symbol
        self.known = known
        super().__init__(f"no underlying for symbol {symbol!r}; known symbols: {known!r}")


class UnknownContractError(UniverseError):
    """A broker contract id was resolved that is not in the universe.

    The broker contract id is treated as an external foreign key; a lookup that misses
    is an explicit failure carrying the id and how many contracts were known, not a
    silent ``None``.
    """

    def __init__(self, broker_contract_id: str, known_count: int) -> None:
        self.broker_contract_id = broker_contract_id
        self.known_count = known_count
        super().__init__(
            f"no instrument for broker contract id {broker_contract_id!r}; "
            f"{known_count} contracts in the universe"
        )


class DuplicateBrokerContractIdError(UniverseError):
    """Two distinct instruments in one universe share a broker contract id.

    The broker contract id is the external foreign key :meth:`UniverseService.resolve_contract`
    keys on, so it must be unique within a universe. Two different canonical instruments
    carrying the same id is a malformed chain (the broker reused a ``conId``); the
    universe refuses it loudly, naming both colliding keys, rather than silently keeping
    the last one in a last-write-wins overwrite.
    """

    def __init__(self, broker_contract_id: str, existing_key: str, conflicting_key: str) -> None:
        self.broker_contract_id = broker_contract_id
        self.existing_key = existing_key
        self.conflicting_key = conflicting_key
        super().__init__(
            f"broker contract id {broker_contract_id!r} maps to two instruments: "
            f"{existing_key!r} and {conflicting_key!r}"
        )


class InstrumentMasterConflictError(UniverseError):
    """A re-materialization resolved an existing key to different stored evidence.

    The raw layer is append-only and immutable: the first materialization for an
    ``(instrument_key, as_of_date)`` is the one that stands. An exact re-run is a no-op
    (idempotent). A run that would change the stored evidence — a different verbatim
    broker payload for the same instrument and date — is a real conflict, surfaced with
    both payloads rather than silently dropped (leaving stale evidence on disk) or
    silently overwritten. Resolving the same instrument from genuinely new broker
    evidence is a new point-in-time row under a new ``as_of_date``, not a rewrite.
    """

    def __init__(
        self,
        instrument_key: str,
        as_of_date: date,
        stored_payload: str,
        incoming_payload: str,
    ) -> None:
        self.instrument_key = instrument_key
        self.as_of_date = as_of_date
        self.stored_payload = stored_payload
        self.incoming_payload = incoming_payload
        super().__init__(
            f"instrument master conflict for {instrument_key!r} as of {as_of_date.isoformat()}: "
            f"stored evidence {stored_payload!r} != incoming {incoming_payload!r}"
        )
