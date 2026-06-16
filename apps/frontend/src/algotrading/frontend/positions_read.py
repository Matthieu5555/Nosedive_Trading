from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime

from algotrading.execution import Fill, booked_position_set
from algotrading.execution.ledger import FillsLedger
from algotrading.infra.contracts import PricingResult
from algotrading.infra.risk import PositionSet

_CONTRACT_KEY_FIELDS = 9
_MULTIPLIER_INDEX = 4


class PositionReadError(Exception):

    def __init__(self, reason: str, *, field: str, value: object) -> None:
        self.reason = reason
        self.field = field
        self.value = value
        super().__init__(f"{field}={value!r}: {reason}")


@dataclass(frozen=True, slots=True)
class GreekComponent:

    raw: float
    position: float
    dollar: float


@dataclass(frozen=True, slots=True)
class PositionLine:

    contract_key: str
    underlying: str
    strike: float | None
    expiry: str | None
    option_right: str | None
    multiplier: float
    quantity: float
    broker_contract_id: str | None
    mark_price: float
    market_value: float
    greeks: Mapping[str, GreekComponent]


@dataclass(frozen=True, slots=True)
class BookGreeks:

    delta: float
    gamma: float
    vega: float
    theta: float
    rho: float
    market_value: float


@dataclass(frozen=True, slots=True)
class PositionBook:

    source: str
    source_ts: datetime
    lines: tuple[PositionLine, ...]
    book: BookGreeks
    priced_contract_keys: int
    unpriced_contract_keys: tuple[str, ...]


_GREEK_NAMES = ("delta", "gamma", "vega", "theta", "rho")


def _multiplier_from_contract_key(contract_key: str) -> float:
    parts = contract_key.split("|")
    if len(parts) < _CONTRACT_KEY_FIELDS:
        return 1.0
    try:
        return float(parts[_MULTIPLIER_INDEX])
    except ValueError as exc:
        raise PositionReadError(
            "multiplier segment is not a number", field="contract_key", value=contract_key
        ) from exc


def _underlying_strike_expiry_right(
    contract_key: str,
) -> tuple[str, float | None, str | None, str | None]:
    parts = contract_key.split("|")
    underlying = parts[0] if parts else contract_key
    if len(parts) < _CONTRACT_KEY_FIELDS:
        return underlying, None, None, None
    expiry = parts[6] or None
    right = parts[8] or None
    strike: float | None
    try:
        strike = float(parts[7]) if parts[7] else None
    except ValueError:
        strike = None
    return underlying, strike, expiry, right


def _latest_pricing_by_key(rows: Iterable[PricingResult]) -> dict[str, PricingResult]:
    latest: dict[str, PricingResult] = {}
    for row in rows:
        prior = latest.get(row.contract_key)
        if prior is None or row.snapshot_ts > prior.snapshot_ts:
            latest[row.contract_key] = row
    return latest


def _line_greeks(
    *, quantity: float, multiplier: float, pricing: PricingResult
) -> dict[str, GreekComponent]:
    scale = quantity * multiplier
    raw = {
        "delta": pricing.delta,
        "gamma": pricing.gamma,
        "vega": pricing.vega,
        "theta": pricing.theta,
        "rho": pricing.rho,
    }
    dollar = {
        "delta": pricing.dollar_delta,
        "gamma": pricing.dollar_gamma,
        "vega": pricing.dollar_vega,
        "theta": 0.0 if pricing.dollar_theta is None else pricing.dollar_theta,
        "rho": 0.0 if pricing.dollar_rho is None else pricing.dollar_rho,
    }
    return {
        name: GreekComponent(
            raw=raw[name],
            position=raw[name] * scale,
            dollar=dollar[name] * quantity,
        )
        for name in _GREEK_NAMES
    }


def _empty_greeks() -> dict[str, GreekComponent]:
    return {name: GreekComponent(raw=0.0, position=0.0, dollar=0.0) for name in _GREEK_NAMES}


def position_book(
    position_set: PositionSet, pricing_rows: Iterable[PricingResult]
) -> PositionBook:
    pricing_by_key = _latest_pricing_by_key(pricing_rows)
    lines: list[PositionLine] = []
    unpriced: list[str] = []
    for pos in position_set.positions:
        quantity = float(pos.quantity)
        multiplier = _multiplier_from_contract_key(pos.contract_key)
        underlying, strike, expiry, right = _underlying_strike_expiry_right(pos.contract_key)
        pricing = pricing_by_key.get(pos.contract_key)
        if pricing is None:
            unpriced.append(pos.contract_key)
            greeks: Mapping[str, GreekComponent] = _empty_greeks()
            mark = 0.0
            market_value = 0.0
        else:
            greeks = _line_greeks(quantity=quantity, multiplier=multiplier, pricing=pricing)
            mark = pricing.price
            market_value = pricing.price * quantity * multiplier
        lines.append(
            PositionLine(
                contract_key=pos.contract_key,
                underlying=underlying,
                strike=strike,
                expiry=expiry,
                option_right=right,
                multiplier=multiplier,
                quantity=quantity,
                broker_contract_id=pos.broker_contract_id,
                mark_price=mark,
                market_value=market_value,
                greeks=greeks,
            )
        )
    book = BookGreeks(
        delta=math.fsum(line.greeks["delta"].dollar for line in lines),
        gamma=math.fsum(line.greeks["gamma"].dollar for line in lines),
        vega=math.fsum(line.greeks["vega"].dollar for line in lines),
        theta=math.fsum(line.greeks["theta"].dollar for line in lines),
        rho=math.fsum(line.greeks["rho"].dollar for line in lines),
        market_value=math.fsum(line.market_value for line in lines),
    )
    return PositionBook(
        source=position_set.source,
        source_ts=position_set.source_ts,
        lines=tuple(lines),
        book=book,
        priced_contract_keys=len(lines) - len(unpriced),
        unpriced_contract_keys=tuple(unpriced),
    )


def booked_position_book(
    ledger: FillsLedger,
    pricing_rows: Iterable[PricingResult],
    *,
    source_ts: datetime,
    trade_date: date | None = None,
    underlying: str | None = None,
) -> PositionBook:
    position_set = booked_position_set(
        ledger, source_ts=source_ts, trade_date=trade_date, underlying=underlying
    )
    return position_book(position_set, pricing_rows)


def fills_view(fills: Iterable[Fill]) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "fill_id": fill.fill_id,
            "booking_id": fill.booking_id,
            "source_basket_id": fill.source_basket_id,
            "trade_date": fill.trade_date.isoformat(),
            "underlying": fill.underlying,
            "contract_key": fill.contract_key,
            "signed_qty": str(fill.signed_qty),
            "price": fill.price,
            "fill_ts": fill.fill_ts.isoformat(),
            "mode": fill.mode,
            "broker_contract_id": fill.broker_contract_id,
        }
        for fill in fills
    )
