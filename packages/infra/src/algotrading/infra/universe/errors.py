from __future__ import annotations

from collections.abc import Mapping
from datetime import date


class UniverseError(Exception):
    pass


class IndexRegistryError(UniverseError):

    def __init__(self, symbol: str, field: str, value: object, reason: str) -> None:
        self.symbol = symbol
        self.field = field
        self.value = value
        self.reason = reason
        super().__init__(
            f"index registry entry {symbol!r}: {field} = {value!r} is invalid: {reason}"
        )


class StrikeSelectionError(UniverseError):

    def __init__(self, field: str, value: object, reason: str) -> None:
        self.field = field
        self.value = value
        self.reason = reason
        super().__init__(f"delta-band strike selection: {field} = {value!r} is invalid: {reason}")


class StrikeWindowClipError(UniverseError):

    def __init__(
        self,
        *,
        configured_window_pct: float,
        required_window_pct: float,
        maturity_years: float,
        working_vol: float,
    ) -> None:
        self.configured_window_pct = configured_window_pct
        self.required_window_pct = required_window_pct
        self.maturity_years = maturity_years
        self.working_vol = working_vol
        super().__init__(
            f"%-of-spot fallback window strike_window_pct={configured_window_pct!r} would clip the "
            f"{required_window_pct:.4f}-of-forward reach of the delta band at maturity_years="
            f"{maturity_years!r}, working_vol={working_vol!r} — refusing to silently trim the band"
        )


class MembershipError(UniverseError):

    def __init__(self, index: str, field: str, value: object, reason: str) -> None:
        self.index = index
        self.field = field
        self.value = value
        self.reason = reason
        super().__init__(
            f"index membership for {index!r}: {field} = {value!r} is invalid: {reason}"
        )


class MembershipRankingError(UniverseError):

    def __init__(self, index: str, field: str, value: object, reason: str) -> None:
        self.index = index
        self.field = field
        self.value = value
        self.reason = reason
        super().__init__(
            f"top-N-by-weight for index {index!r}: {field} = {value!r} is invalid: {reason}"
        )


class CalendarResolutionError(UniverseError):

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

    def __init__(self, payload: Mapping[str, object], field: str, reason: str) -> None:
        self.payload = dict(payload)
        self.field = field
        self.reason = reason
        super().__init__(f"unresolved contract: {field}: {reason}; payload={self.payload!r}")


class UnknownInstrumentError(UniverseError):

    def __init__(self, symbol: str, known: tuple[str, ...]) -> None:
        self.symbol = symbol
        self.known = known
        super().__init__(f"no underlying for symbol {symbol!r}; known symbols: {known!r}")


class UnknownContractError(UniverseError):

    def __init__(self, broker_contract_id: str, known_count: int) -> None:
        self.broker_contract_id = broker_contract_id
        self.known_count = known_count
        super().__init__(
            f"no instrument for broker contract id {broker_contract_id!r}; "
            f"{known_count} contracts in the universe"
        )


class DuplicateBrokerContractIdError(UniverseError):

    def __init__(self, broker_contract_id: str, existing_key: str, conflicting_key: str) -> None:
        self.broker_contract_id = broker_contract_id
        self.existing_key = existing_key
        self.conflicting_key = conflicting_key
        super().__init__(
            f"broker contract id {broker_contract_id!r} maps to two instruments: "
            f"{existing_key!r} and {conflicting_key!r}"
        )


class InstrumentMasterConflictError(UniverseError):

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
