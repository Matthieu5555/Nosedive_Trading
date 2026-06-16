from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

_SEP = ":"
_OPTION_TAG = "OPT"
_UNDERLYING_TAG = "UND"
EXPIRY_FMT = "%Y%m%d"


class InstrumentKeyError(ValueError):
    pass


class Right(StrEnum):

    CALL = "C"
    PUT = "P"

    @classmethod
    def from_raw(cls, value: str) -> Right:
        token = value.strip().upper()
        if token in ("C", "CALL"):
            return cls.CALL
        if token in ("P", "PUT"):
            return cls.PUT
        raise InstrumentKeyError(f"unknown option right: {value!r}")


def _require(value: str, field_name: str) -> str:
    text = value.strip()
    if not text:
        raise ValueError(f"{field_name} must be non-empty")
    if _SEP in text:
        raise InstrumentKeyError(f"{field_name} may not contain {_SEP!r}: {value!r}")
    return text


@dataclass(frozen=True)
class Underlying:

    symbol: str
    exchange: str
    currency: str
    security_type: str = "STK"

    def __post_init__(self) -> None:
        _require(self.symbol, "symbol")
        _require(self.security_type, "security_type")
        _require(self.exchange, "exchange")
        _require(self.currency, "currency")


@dataclass(frozen=True)
class OptionContract:

    symbol: str
    expiry: date
    strike: Decimal
    right: Right
    multiplier: int
    exchange: str
    currency: str
    security_type: str = "OPT"
    trading_class: str | None = field(default=None, compare=False)
    broker_contract_id: str | None = field(default=None, compare=False)
    raw: Mapping[str, object] | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        _require(self.symbol, "symbol")
        _require(self.security_type, "security_type")
        _require(self.exchange, "exchange")
        _require(self.currency, "currency")
        if self.multiplier <= 0:
            raise ValueError(f"multiplier must be > 0, got {self.multiplier}")
        if not self.strike.is_finite():
            raise ValueError(f"strike must be finite, got {self.strike}")
        if self.strike <= 0:
            raise ValueError(f"strike must be > 0, got {self.strike}")


def _canonical_strike(strike: Decimal) -> str:
    return format(strike.normalize(), "f")


def instrument_key(instrument: Underlying | OptionContract) -> str:
    if isinstance(instrument, Underlying):
        parts = [
            _UNDERLYING_TAG,
            instrument.symbol,
            instrument.security_type,
            instrument.exchange,
            instrument.currency,
        ]
        return _SEP.join(parts)
    parts = [
        _OPTION_TAG,
        instrument.symbol,
        instrument.security_type,
        instrument.expiry.strftime(EXPIRY_FMT),
        instrument.right.value,
        _canonical_strike(instrument.strike),
        str(instrument.multiplier),
        instrument.exchange,
        instrument.currency,
    ]
    return _SEP.join(parts)


def parse_instrument_key(key: str) -> Underlying | OptionContract:
    parts = key.split(_SEP)
    tag = parts[0] if parts else ""
    try:
        if tag == _UNDERLYING_TAG:
            _, symbol, security_type, exchange, currency = parts
            return Underlying(
                symbol=symbol,
                security_type=security_type,
                exchange=exchange,
                currency=currency,
            )
        if tag == _OPTION_TAG:
            _, symbol, security_type, expiry_s, right_s, strike_s, mult_s, exchange, currency = (
                parts
            )
            return OptionContract(
                symbol=symbol,
                security_type=security_type,
                expiry=datetime.strptime(expiry_s, EXPIRY_FMT).date(),
                strike=Decimal(strike_s),
                right=Right(right_s),
                multiplier=int(mult_s),
                exchange=exchange,
                currency=currency,
            )
    except (ValueError, ArithmeticError) as exc:
        raise InstrumentKeyError(f"malformed key {key!r}: {exc}") from exc
    raise InstrumentKeyError(f"unknown key tag {tag!r} in {key!r}")
