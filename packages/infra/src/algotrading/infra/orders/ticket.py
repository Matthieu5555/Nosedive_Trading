"""Order ticket: a typed, validated, serializable ticket built *purely* from a 2A basket.

**This is WS 3A — preview/build only, paper/read-only, NO transmission.** Nothing here
connects to a broker, reads a credential, or places an order. The ticket is the inert object
WS 3B will later *sign and send* behind an explicit owner gate; building it is a **pure**
function (no I/O, no clock, no network).

Leg identity mirrors the 2A :class:`~algotrading.infra.contracts.BasketLeg` — an option leg
names its grid cell ``(underlying, tenor_label, delta_band)``, a stock leg names the underlying.
That is deliberate and grounded in the plan-of-record sources: the course composes strategies by
**tenor / delta-band / ATM** (dispersion straddles, calendars) and defers the actual order to
"*signer l'ordre*"; the blueprint keys a real *position* by a **concrete** ``contract_key``
(underlying, strike, expiry, right). So the concrete-contract binding (strike / expiry / broker
``conid``) is **3B's**, done when the order is signed — not in this pure builder, which would
otherwise need to read the chain. A ``# 3B:`` marker sits where that resolution attaches.

Side convention: the basket already carries the economic direction as ``long``/``short`` with a
sign-consistent quantity (the single source of truth — we do not invent a parallel side shape).
The ticket maps it to an **order** side — ``long`` opens with :attr:`Side.BUY`, ``short`` with
:attr:`Side.SELL` — and carries a **positive** quantity (the magnitude; the side carries the
direction). Every malformed construction is a labelled :class:`TicketError`, never a bare
exception and never a silent default.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from enum import Enum

from algotrading.infra.contracts import Basket, BasketLeg


class TicketError(Exception):
    """A labelled rejection of a malformed ticket construction.

    Carries the offending ``field`` and ``value`` alongside a human ``reason`` so the caller
    (and the BFF) can surface *what* was wrong, not an opaque ``KeyError``/``ValueError``.
    """

    def __init__(self, reason: str, *, field: str, value: object) -> None:
        self.reason = reason
        self.field = field
        self.value = value
        super().__init__(f"{field}={value!r}: {reason}")


class Side(Enum):
    """Order side. The basket's ``long``/``short`` maps here; quantity stays positive."""

    BUY = "buy"
    SELL = "sell"


class TimeInForce(Enum):
    """How long the order rests. A small closed set; extend when 3B needs more."""

    DAY = "day"
    GTC = "gtc"


class TargetBroker(Enum):
    """The broker the ticket targets. IBKR is the sole live broker (ADR 0042); kept an
    enum so another broker can rejoin without reshaping the contract."""

    IBKR = "ibkr"


@dataclass(frozen=True, slots=True)
class Market:
    """Trade at the prevailing market price — carries no price by construction."""


@dataclass(frozen=True, slots=True)
class Limit:
    """Trade at no worse than ``price`` — a price is required and must be finite and positive."""

    price: float

    def __post_init__(self) -> None:
        import math

        if not math.isfinite(self.price):
            raise TicketError("a limit price must be finite", field="price", value=self.price)
        if self.price <= 0:
            raise TicketError("a limit price must be positive", field="price", value=self.price)


# The price specification is a *closed set*: exactly Market or Limit(price). A limit with no
# price, or a market carrying a price, is unrepresentable — invalid by construction, not by check.
PriceSpec = Market | Limit


@dataclass(frozen=True, slots=True)
class TicketLeg:
    """One leg of an order ticket: a sided, positive-quantity order over a basket leg's identity.

    The identity fields mirror :class:`BasketLeg` exactly (an option leg names its grid cell;
    a stock leg names the underlying) — the same shape, never a re-parsed parallel one.
    """

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
            # The side carries direction; the ticket quantity is the positive magnitude.
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
    """A built, validated, inert order ticket — the object 3B signs and sends.

    ``mode`` is pinned ``"paper"``: transmission is structurally absent from this module. The
    ticket carries provenance back to the originating basket (``source_basket_id``) so a booked
    position can be traced to the intention that created it.
    """

    source_basket_id: str
    trade_date: date
    underlying: str
    target_broker: TargetBroker
    time_in_force: TimeInForce
    legs: tuple[TicketLeg, ...]
    mode: str = field(default="paper")

    def __post_init__(self) -> None:
        if self.mode != "paper":
            # 3B owns the live path behind the owner gate; 3A is paper-only by construction.
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
    """``long`` opens with a BUY, ``short`` with a SELL — the basket's side is the source."""
    return Side.BUY if leg.side == "long" else Side.SELL


def build_ticket(
    basket: Basket,
    *,
    broker: TargetBroker = TargetBroker.IBKR,
    tif: TimeInForce = TimeInForce.DAY,
    price_spec: PriceSpec | None = None,
    price_spec_by_leg: Sequence[PriceSpec] | None = None,
) -> OrderTicket:
    """Map a 2A :class:`Basket` to a validated :class:`OrderTicket` — **pure**, no I/O.

    Each basket leg becomes a ticket leg: side from the basket's ``long``/``short``, a positive
    quantity (the magnitude of the signed basket quantity), and a price spec. ``price_spec``
    applies one spec to every leg (default :class:`Market`); ``price_spec_by_leg`` instead gives
    one spec per leg, in basket order — pass at most one of the two.

    Raises a labelled :class:`TicketError` on: an empty basket, an unknown broker/TIF, a
    ``price_spec_by_leg`` whose length does not match the legs, a duplicate leg, or any value the
    :class:`TicketLeg`/:class:`OrderTicket` contracts reject. The broker is **named and
    validated** here — never connected to; the concrete-contract resolution and any transmission
    are 3B's.
    """
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
        # 3B: the concrete (strike, expiry, broker conid) for this grid cell is bound here, at
        # sign-and-send time — never in this pure builder.
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
