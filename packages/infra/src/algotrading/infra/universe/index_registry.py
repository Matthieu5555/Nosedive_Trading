from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import time
from typing import Annotated, NoReturn

import exchange_calendars as xcals
from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
)

from .errors import IndexRegistryError


def _require_non_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("must be a non-empty string")
    return value


def _require_iso_currency(value: str) -> str:
    if not (len(value) == 3 and value.isalpha() and value.isupper()):
        raise ValueError("must be a 3-letter uppercase ISO code")
    return value


_NonBlankStr = Annotated[str, AfterValidator(_require_non_blank)]


class _IbkrRefModel(BaseModel):

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    conid: int = Field(ge=0)
    sec_type: str = Field(alias="secType")
    exchange: _NonBlankStr
    symbol: _NonBlankStr | None = None
    constituent_conids: Annotated[
        dict[_NonBlankStr, Annotated[int, Field(gt=0)]],
        BeforeValidator(lambda value: {} if value is None else value),
    ] = Field(default_factory=dict)

    @field_validator("sec_type")
    @classmethod
    def _sec_type_non_blank(cls, value: str) -> str:
        return _require_non_blank(value)


class _IndexEntryModel(BaseModel):

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    name: _NonBlankStr
    calendar: str
    option_settlement_close: str | None = None
    currency: Annotated[str, AfterValidator(_require_iso_currency)]
    ibkr: _IbkrRefModel
    enabled: bool

    @field_validator("calendar")
    @classmethod
    def _known_calendar(cls, value: str, info: ValidationInfo) -> str:
        _require_non_blank(value)
        known: frozenset[str] = (info.context or {}).get("known_calendars", frozenset())
        if value not in known:
            raise ValueError(
                "unknown exchange_calendars code (not in get_calendar_names()); "
                "an unknown calendar is never defaulted"
            )
        return value

    @field_validator("option_settlement_close")
    @classmethod
    def _valid_settlement_close(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            time.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("must be an ISO 24-hour time-of-day 'HH:MM' (e.g. '17:30')") from exc
        return value


@dataclass(frozen=True, slots=True)
class IbkrRef:

    conid: int
    sec_type: str
    exchange: str
    symbol: str | None = None
    constituent_conids: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True, slots=True)
class IndexEntry:

    symbol: str
    name: str
    calendar: str
    currency: str
    ibkr: IbkrRef
    enabled: bool
    option_settlement_close: time | None = None

    @property
    def ibkr_search_symbol(self) -> str:
        return self.ibkr.symbol or self.symbol


@dataclass(frozen=True, slots=True)
class IndexRegistry:

    entries: tuple[IndexEntry, ...]

    def __post_init__(self) -> None:
        symbols = [entry.symbol for entry in self.entries]
        if len(set(symbols)) != len(symbols):
            dupes = sorted({s for s in symbols if symbols.count(s) > 1})
            raise IndexRegistryError(
                dupes[0], "symbol", dupes[0], "duplicate index symbol in registry"
            )

    def get(self, symbol: str) -> IndexEntry:
        for entry in self.entries:
            if entry.symbol == symbol:
                return entry
        known = tuple(sorted(e.symbol for e in self.entries))
        raise IndexRegistryError(symbol, "symbol", symbol, f"not in registry; known: {known!r}")

    def enabled_indices(self) -> tuple[IndexEntry, ...]:
        return tuple(sorted((e for e in self.entries if e.enabled), key=lambda e: e.symbol))


def _raise_registry_error(symbol: str, exc: ValidationError) -> NoReturn:
    error = exc.errors()[0]
    location = error.get("loc", ())
    parts = [str(part) for part in location if part != "[key]"]
    field = ".".join(parts) if parts else "<entry>"
    kind = error.get("type", "")
    if kind == "missing":
        raise IndexRegistryError(symbol, field, None, "missing field") from exc
    if kind == "extra_forbidden":
        raise IndexRegistryError(symbol, field, error.get("input"), "unknown key") from exc
    raise IndexRegistryError(symbol, field, error.get("input"), error.get("msg", "")) from exc


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    return value


def _parse_entry(symbol: str, raw: object, known_calendars: frozenset[str]) -> IndexEntry:
    if not symbol or not symbol.strip():
        raise IndexRegistryError(symbol, "symbol", symbol, "must be a non-empty string")
    try:
        model = _IndexEntryModel.model_validate(
            _thaw(raw), context={"known_calendars": known_calendars}
        )
    except ValidationError as exc:
        _raise_registry_error(symbol, exc)
    return IndexEntry(
        symbol=symbol,
        name=model.name,
        calendar=model.calendar,
        currency=model.currency,
        option_settlement_close=(
            time.fromisoformat(model.option_settlement_close)
            if model.option_settlement_close is not None
            else None
        ),
        ibkr=IbkrRef(
            conid=model.ibkr.conid,
            sec_type=model.ibkr.sec_type,
            exchange=model.ibkr.exchange,
            symbol=model.ibkr.symbol,
            constituent_conids=tuple(model.ibkr.constituent_conids.items()),
        ),
        enabled=model.enabled,
    )


def parse_index_registry(block: Mapping[str, object] | None) -> IndexRegistry:
    if block is None:
        return IndexRegistry(entries=())
    if not isinstance(block, Mapping):
        raise IndexRegistryError("<indices>", "<root>", block, "must be a mapping")
    known_calendars = frozenset(xcals.get_calendar_names())
    entries = tuple(
        _parse_entry(str(symbol), raw, known_calendars)
        for symbol, raw in sorted(block.items(), key=lambda kv: str(kv[0]))
    )
    return IndexRegistry(entries=entries)
