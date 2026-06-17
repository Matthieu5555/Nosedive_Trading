from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from .decision import TransmissionDecision
from .signing import SignedTicket


@dataclass(frozen=True, slots=True)
class SinkOutcome:

    decision: TransmissionDecision
    venue_ack: str | None
    detail: str


@runtime_checkable
class TransmitSink(Protocol):

    def handle(
        self, signed: SignedTicket, decision: TransmissionDecision, now: datetime
    ) -> SinkOutcome: ...


class PaperSink:

    def __init__(self) -> None:
        self.recorded: list[tuple[str, TransmissionDecision]] = []

    def handle(
        self, signed: SignedTicket, decision: TransmissionDecision, now: datetime
    ) -> SinkOutcome:
        self.recorded.append((signed.binding_hash, decision))
        if decision is TransmissionDecision.SENT_PAPER:
            return SinkOutcome(
                decision=decision,
                venue_ack=None,
                detail="recorded paper transmission; no bytes left the process",
            )
        return SinkOutcome(
            decision=decision,
            venue_ack=None,
            detail=f"blocked ({decision.value}); no bytes left the process",
        )
