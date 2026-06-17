from __future__ import annotations

from algotrading.core.hashing import canonical_dumps, sha256_hex
from algotrading.infra.orders import Limit, Market, OrderTicket, PriceSpec, TicketLeg


def _price_spec_payload(spec: PriceSpec) -> dict[str, object]:
    if isinstance(spec, Limit):
        return {"kind": "limit", "price": repr(spec.price)}
    if isinstance(spec, Market):
        return {"kind": "market"}
    raise TypeError(f"unknown price spec: {spec!r}")


def _leg_payload(leg: TicketLeg) -> dict[str, object]:
    return {
        "instrument_kind": leg.instrument_kind,
        "underlying": leg.underlying,
        "side": leg.side.value,
        "quantity": repr(leg.quantity),
        "price_spec": _price_spec_payload(leg.price_spec),
        "tenor_label": leg.tenor_label,
        "delta_band": leg.delta_band,
    }


def ticket_binding_payload(ticket: OrderTicket) -> dict[str, object]:
    return {
        "source_basket_id": ticket.source_basket_id,
        "trade_date": ticket.trade_date.isoformat(),
        "underlying": ticket.underlying,
        "target_broker": ticket.target_broker.value,
        "time_in_force": ticket.time_in_force.value,
        "mode": ticket.mode,
        "legs": [_leg_payload(leg) for leg in ticket.legs],
    }


def ticket_binding_hash(ticket: OrderTicket) -> str:
    return sha256_hex(canonical_dumps(ticket_binding_payload(ticket)))
