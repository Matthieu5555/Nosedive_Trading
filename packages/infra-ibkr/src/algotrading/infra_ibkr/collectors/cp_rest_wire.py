from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError

_NO_VALUE = -1.0

SNAPSHOT_FIELD_TAGS: tuple[str, ...] = ("84", "86", "88", "85", "31", "7059", "7762")


def parse_field_value(raw: object) -> float | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if text and not (text[0].isdigit() or text[0] in "+-."):
        text = text[1:].strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    if not math.isfinite(value) or value == _NO_VALUE:
        return None
    return value


def coerce_int_or_none(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if math.isfinite(value) else None
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def coerce_text(value: object) -> str:
    return "" if value is None else str(value)


def require_bar_number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"must be numeric, got {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"is not finite: {value!r}")
    return number


def keep_mappings(value: object) -> tuple[object, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


MarketFieldValue = Annotated[float | None, BeforeValidator(parse_field_value)]
LooseInt = Annotated[int | None, BeforeValidator(coerce_int_or_none)]
BarNumber = Annotated[float, BeforeValidator(require_bar_number)]
WireText = Annotated[str, BeforeValidator(coerce_text)]

_WIRE_MODEL_CONFIG = ConfigDict(extra="ignore", frozen=True)


class SnapshotRow(BaseModel):

    model_config = _WIRE_MODEL_CONFIG

    conid: LooseInt = None
    updated_ms: LooseInt = Field(default=None, alias="_updated")
    bid: MarketFieldValue = Field(default=None, alias="84")
    ask: MarketFieldValue = Field(default=None, alias="86")
    bid_size: MarketFieldValue = Field(default=None, alias="88")
    ask_size: MarketFieldValue = Field(default=None, alias="85")
    last: MarketFieldValue = Field(default=None, alias="31")
    last_size: MarketFieldValue = Field(default=None, alias="7059")
    volume: MarketFieldValue = Field(default=None, alias="7762")

    def has_market_value(self) -> bool:
        values = (self.bid, self.ask, self.bid_size, self.ask_size, self.last, self.last_size)
        return any(value is not None for value in values)

    def spot_value(self) -> float | None:
        for value in (self.last, self.bid, self.ask):
            if value is not None and value > 0.0:
                return value
        return None


def parse_snapshot_rows(rows: object) -> tuple[SnapshotRow, ...]:
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return ()
    return tuple(SnapshotRow.model_validate(row) for row in rows if isinstance(row, Mapping))


class SecdefSection(BaseModel):

    model_config = _WIRE_MODEL_CONFIG

    sec_type: WireText = Field(default="", alias="secType")
    exchange: WireText = ""
    months: WireText = ""


class SecdefSearchRow(BaseModel):

    model_config = _WIRE_MODEL_CONFIG

    symbol: WireText = ""
    conid: int | None = None
    description: WireText = ""
    sec_type: WireText = Field(default="", alias="secType")
    sections: Annotated[tuple[SecdefSection, ...], BeforeValidator(keep_mappings)] = ()


def parse_secdef_search_rows(results: object) -> tuple[SecdefSearchRow, ...]:
    if not isinstance(results, Sequence) or isinstance(results, (str, bytes)):
        return ()
    rows: list[SecdefSearchRow] = []
    for item in results:
        if not isinstance(item, Mapping):
            continue
        try:
            rows.append(SecdefSearchRow.model_validate(item))
        except ValidationError:
            continue
    return tuple(rows)


class StrikesPayload(BaseModel):

    model_config = _WIRE_MODEL_CONFIG

    call: tuple[float, ...] = ()
    put: tuple[float, ...] = ()


class SecdefInfoRow(BaseModel):

    model_config = _WIRE_MODEL_CONFIG

    conid: WireText
    maturity_date: WireText = Field(alias="maturityDate")
    strike: WireText
    right: WireText


class HistoryBarRow(BaseModel):

    model_config = _WIRE_MODEL_CONFIG

    time_ms: BarNumber = Field(alias="t")
    open_price: BarNumber = Field(alias="o")
    high: BarNumber = Field(alias="h")
    low: BarNumber = Field(alias="l")
    close: BarNumber = Field(alias="c")
    volume: BarNumber = Field(alias="v")
