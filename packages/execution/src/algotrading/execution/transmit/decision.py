from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from enum import Enum

from .gate import MODE_ABSENT, MODE_LIVE, MODE_PAPER, GateLoad, GateUnparseable, TransmitGate
from .signing import (
    SignedTicket,
    binds_ticket,
    signoff_token_valid_from_environment,
    signoff_unexpired,
)


class TransmissionDecision(Enum):

    BLOCKED_DEFAULT = "blocked_default"
    BLOCKED_NO_SIGNOFF = "blocked_no_signoff"
    BLOCKED_GATE_OFF = "blocked_gate_off"
    BLOCKED_EXPIRED = "blocked_expired"
    BLOCKED_TICKET_MISMATCH = "blocked_ticket_mismatch"
    SENT_PAPER = "sent_paper"
    SENT_LIVE = "sent_live"


SignoffVerifier = Callable[[SignedTicket], bool]


def decide_transmission(
    signed: SignedTicket,
    gate: GateLoad,
    now: datetime,
    *,
    verify_signoff: SignoffVerifier = signoff_token_valid_from_environment,
) -> TransmissionDecision:
    if not isinstance(gate, TransmitGate) or isinstance(gate, GateUnparseable):
        return TransmissionDecision.BLOCKED_DEFAULT
    if gate.mode == MODE_ABSENT:
        return TransmissionDecision.BLOCKED_DEFAULT

    if not binds_ticket(signed, signed.ticket):
        return TransmissionDecision.BLOCKED_TICKET_MISMATCH
    if not verify_signoff(signed):
        return TransmissionDecision.BLOCKED_NO_SIGNOFF
    if not signoff_unexpired(signed, now):
        return TransmissionDecision.BLOCKED_EXPIRED

    if gate.mode == MODE_PAPER:
        return TransmissionDecision.SENT_PAPER

    if gate.mode == MODE_LIVE:
        if not gate.security_review_green:
            return TransmissionDecision.BLOCKED_GATE_OFF
        return TransmissionDecision.SENT_LIVE

    return TransmissionDecision.BLOCKED_DEFAULT  # pragma: no cover - exhaustiveness guard
