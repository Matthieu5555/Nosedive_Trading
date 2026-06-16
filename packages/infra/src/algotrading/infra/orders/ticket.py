from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from enum import Enum

from algotrading.infra.contracts import Basket, BasketLeg


class TicketError(Exception):

    def __init__(self, reason: str, *, field: str, value: object) -> None:
        self.reason = reason
        self.field = field
        self.value = value
        super().__init__(f"{field}={value!r}: {reason}")


class Side(Enum):

    BUY = "buy"
    SELL = "sell"


class TimeInForce(Enum):

    DAY = "day"
    GTC = "gtc"


class TargetBroker(Enum):

    IBKR = "ibkr"


@dataclass(frozen=True, slots=True)
class Market:
    pass


@dataclass(frozen=True, slots=True)
class Limit:

    price: float

    def __post_init__(self) -> None:
        import math

        if not math.isfinite(self.price):
            raise TicketError("a limit price must be finite", field="price", value=self.price)
        if self.price <= 0:
            raise TicketError("a limit price must be positive", field="price", value=self.price)


PriceSpec = Market | Limit


@dataclass(frozen=True, slots=True)
class TicketLeg:

    instrument_kind: str
    underlying: str
    side: Side
    quantity: float
    price_spec: PriceSpec
    tenor_label: str | None = None
    delta_band: str | None = None

    def __post_init__(self) -> None:
        import math

        if not isinstance(self.side, Side):
            raise TicketError("side must be a Side", field="side", value=self.side)
        if not isinstance(self.price_spec, (Market, Limit)):
            raise TicketError(
                "price_spec must be Market or Limit", field="price_spec", value=self.price_spec
            )
        if not math.isfinite(self.quantity):
            raise TicketError("quantity must be finite", field="quantity", value=self.quantity)
        if self.quantity <= 0:
            raise TicketError(
                "quantity must be positive (the side carries direction)",
                field="quantity",
                value=self.quantity,
            )
        if self.instrument_kind == "option" and (
            self.tenor_label is None or self.delta_band is None
        ):
            raise TicketError(
                "an option leg must name its grid cell (tenor_label and delta_band)",
                field="tenor_label",
                value=(self.tenor_label, self.delta_band),
            )
        if self.instrument_kind == "stock" and (
            self.tenor_label is not None or self.delta_band is not None
        ):
            raise TicketError(
                "a stock leg has no tenor/band (both must be None)",
                field="tenor_label",
                value=(self.tenor_label, self.delta_band),
            )


@dataclass(frozen=True, slots=True)
class OrderTicket:

    source_basket_id: str
    trade_date: date
    underlying: str
    target_broker: TargetBroker
    time_in_force: TimeInForce
    legs: tuple[TicketLeg, ...]
    mode: str = field(default="paper")

    def __post_init__(self) -> None:
        if self.mode != "paper":
            raise TicketError("3A tickets are paper-only", field="mode", value=self.mode)
        if not isinstance(self.target_broker, TargetBroker):
            raise TicketError(
                "unknown target broker", field="target_broker", value=self.target_broker
            )
        if not isinstance(self.time_in_force, TimeInForce):
            raise TicketError(
                "unknown time-in-force", field="time_in_force", value=self.time_in_force
            )
        if not self.legs:
            raise TicketError("a ticket needs at least one leg", field="legs", value=self.legs)


def _order_side(leg: BasketLeg) -> Side:
    return Side.BUY if leg.side == "long" else Side.SELL


def build_ticket(
    basket: Basket,
    *,
    broker: TargetBroker = TargetBroker.IBKR,
    tif: TimeInForce = TimeInForce.DAY,
    price_spec: PriceSpec | None = None,
    price_spec_by_leg: Sequence[PriceSpec] | None = None,
) -> OrderTicket:
    if not isinstance(broker, TargetBroker):
        raise TicketError("unknown target broker", field="broker", value=broker)
    if not isinstance(tif, TimeInForce):
        raise TicketError("unknown time-in-force", field="tif", value=tif)
    if not basket.legs:
        raise TicketError("cannot build a ticket from an empty basket", field="legs", value=())
    if price_spec is not None and price_spec_by_leg is not None:
        raise TicketError(
            "pass at most one of price_spec / price_spec_by_leg",
            field="price_spec",
            value=(price_spec, price_spec_by_leg),
        )
    if price_spec_by_leg is not None and len(price_spec_by_leg) != len(basket.legs):
        raise TicketError(
            "price_spec_by_leg must have one spec per basket leg",
            field="price_spec_by_leg",
            value=(len(price_spec_by_leg), len(basket.legs)),
        )

    default_spec: PriceSpec = price_spec if price_spec is not None else Market()

    legs: list[TicketLeg] = []
    seen: set[tuple[object, ...]] = set()
    for index, leg in enumerate(basket.legs):
        identity = (leg.instrument_kind, leg.underlying, leg.tenor_label, leg.delta_band, leg.side)
        if identity in seen:
            raise TicketError("duplicate leg", field="legs", value=identity)
        seen.add(identity)
        spec = price_spec_by_leg[index] if price_spec_by_leg is not None else default_spec
        legs.append(
            TicketLeg(
                instrument_kind=leg.instrument_kind,
                underlying=leg.underlying,
                side=_order_side(leg),
                quantity=abs(leg.quantity),
                price_spec=spec,
                tenor_label=leg.tenor_label,
                delta_band=leg.delta_band,
            )
        )

    return OrderTicket(
        source_basket_id=basket.basket_id,
        trade_date=basket.trade_date,
        underlying=basket.underlying,
        target_broker=broker,
        time_in_force=tif,
        legs=tuple(legs),
    )
