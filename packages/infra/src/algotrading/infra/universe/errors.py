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


class IndexRegistryError(UniverseError):
    """An `indices:` registry entry was malformed and is rejected, not coerced.

    Carries the offending index ``symbol``, the ``field`` that failed, the ``value``
    seen, and a plain-language ``reason``, so a bad registry entry names exactly what
    was wrong (an unknown calendar code, an empty symbol, a non-bool ``enabled``)
    instead of being silently defaulted — the load-bearing rule for the calendar code,
    where a silent fallback would capture the wrong close instant (a look-ahead bug).
    """

    def __init__(self, symbol: str, field: str, value: object, reason: str) -> None:
        self.symbol = symbol
        self.field = field
        self.value = value
        self.reason = reason
        super().__init__(
            f"index registry entry {symbol!r}: {field} = {value!r} is invalid: {reason}"
        )


class StrikeSelectionError(UniverseError):
    """A delta-band strike selection was asked to price with an unusable input (WS 1B).

    Raised by :func:`~algotrading.infra.universe.chain_planning.select_strikes_delta_band`
    when the per-tenor pricing inputs cannot yield a delta: a missing/zero/non-finite
    forward, a non-finite or negative working volatility, a non-positive maturity, or a
    discount factor outside ``(0, 1]``. Carries the offending ``field``, the ``value``
    seen, and a plain-language ``reason``, so the caller gets a *labeled* failure rather
    than a bare ``NaN`` strike silently entering the captured chain (the TESTING.md
    negative-path floor and a look-ahead/quality risk if a poisoned strike were kept).
    """

    def __init__(self, field: str, value: object, reason: str) -> None:
        self.field = field
        self.value = value
        self.reason = reason
        super().__init__(f"delta-band strike selection: {field} = {value!r} is invalid: {reason}")


class MembershipError(UniverseError):
    """A dated index-membership change is malformed and is rejected, not coerced (WS 1A).

    Raised by the membership ingester before any row is written: a negative weight, an
    ``effective_remove_date`` earlier than its ``effective_add_date``, an empty index or
    constituent symbol, or a basket whose source-complete weights do not sum near 1.0.
    Carries the offending ``index``, the ``field`` that failed, the ``value`` seen, and a
    plain-language ``reason``, so a bad change names exactly what was wrong instead of
    being silently dropped or zeroed (a silent default would be an economic-correctness
    bug and a TESTING.md negative-path failure).
    """

    def __init__(self, index: str, field: str, value: object, reason: str) -> None:
        self.index = index
        self.field = field
        self.value = value
        self.reason = reason
        super().__init__(
            f"index membership for {index!r}: {field} = {value!r} is invalid: {reason}"
        )


class MembershipRankingError(UniverseError):
    """A top-N-by-weight selection could not rank the basket, for a labeled reason (S1).

    Raised by :func:`~algotrading.infra.universe.membership.top_n_by_weight` when the basket
    cannot be ranked deterministically by index weight: a non-positive ``n`` (asking for the
    top-zero or top-negative names is meaningless), or a basket carrying any *labeled-
    unavailable* (``None``) weight — you cannot rank what isn't known, and silently dropping
    or zeroing the missing names would bias the selection (the economic-correctness bug the
    membership layer refuses everywhere). Carries the offending ``index``, the ``field`` that
    failed, the ``value`` seen, and a plain-language ``reason``, so the caller gets a *labeled*
    failure naming exactly what blocked the rank rather than a quietly-truncated basket.
    """

    def __init__(self, index: str, field: str, value: object, reason: str) -> None:
        self.index = index
        self.field = field
        self.value = value
        self.reason = reason
        super().__init__(
            f"top-N-by-weight for index {index!r}: {field} = {value!r} is invalid: {reason}"
        )


class CalendarResolutionError(UniverseError):
    """A calendar resolve failed for a labeled reason rather than a silent wrong answer.

    Raised when the resolver is asked about an index whose calendar code is not in the
    registry, or for a date outside the calendar library's coverage window, or for the
    session close of a date that is not a trading session. Carries the ``index``, the
    ``calendar`` code, the offending ``date_`` (when relevant), and a ``reason`` — never
    a bare library traceback and never a silently-defaulted instant.
    """

    def __init__(
        self, index: str, calendar: str, date_: date | None, reason: str
    ) -> None:
        self.index = index
        self.calendar = calendar
        self.date_ = date_
        self.reason = reason
        on = f" on {date_.isoformat()}" if date_ is not None else ""
        super().__init__(
            f"calendar resolve for index {index!r} (calendar {calendar!r}){on}: {reason}"
        )


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
