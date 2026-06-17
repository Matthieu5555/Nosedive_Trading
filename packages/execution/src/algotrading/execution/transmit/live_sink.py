from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from .decision import TransmissionDecision
from .signing import SignedTicket
from .sink import SinkOutcome


@runtime_checkable
class OrderSubmitter(Protocol):

    def submit(self, order: dict[str, Any]) -> object: ...


def _order_from_ticket(signed: SignedTicket) -> dict[str, Any]:
    legs = [
        {
            "side": leg.side.value,
            "quantity": leg.quantity,
            "underlying": leg.underlying,
            "tenor_label": leg.tenor_label,
            "delta_band": leg.delta_band,
        }
        for leg in signed.ticket.legs
    ]
    return {
        "binding_hash": signed.binding_hash,
        "tif": signed.ticket.time_in_force.value,
        "legs": legs,
    }


class LiveSubmitSink:

    def __init__(self, submitter: OrderSubmitter) -> None:
        self._submitter = submitter

    def handle(
        self, signed: SignedTicket, decision: TransmissionDecision, now: datetime
    ) -> SinkOutcome:
        if decision is not TransmissionDecision.SENT_LIVE:
            return SinkOutcome(
                decision=decision,
                venue_ack=None,
                detail=f"blocked ({decision.value}); no bytes left the process",
            )
        ack = self._submitter.submit(_order_from_ticket(signed))
        return SinkOutcome(
            decision=decision,
            venue_ack=str(ack),
            detail="submitted to the live broker seam",
        )
