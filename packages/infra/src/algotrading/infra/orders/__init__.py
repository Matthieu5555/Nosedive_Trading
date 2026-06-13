"""Order tickets (WS 3A): a pure basket->ticket model, preview-only and paper/read-only.

Transmission is structurally absent — sending a ticket is WS 3B, behind an explicit owner gate.
This package names and validates the target broker; it never connects to one.
"""

from __future__ import annotations

from .ticket import (
    Limit,
    Market,
    OrderTicket,
    PriceSpec,
    Side,
    TargetBroker,
    TicketError,
    TicketLeg,
    TimeInForce,
    build_ticket,
)

__all__ = [
    "Limit",
    "Market",
    "OrderTicket",
    "PriceSpec",
    "Side",
    "TargetBroker",
    "TicketError",
    "TicketLeg",
    "TimeInForce",
    "build_ticket",
]
