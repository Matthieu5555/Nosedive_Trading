from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from algotrading.execution.transmit import (
    InMemoryTransmitAuditLog,
    SignedTicket,
    TransmissionDecision,
    issue_token,
    ticket_binding_hash,
    transmit,
)
from algotrading.execution.transmit.gate import load_transmit_gate
from algotrading.execution.transmit.live_sink import LiveSubmitSink, OrderSubmitter
from algotrading.infra.contracts import Basket, BasketLeg
from algotrading.infra.orders import OrderTicket, build_ticket

ISSUED = datetime(2026, 6, 12, 9, 0, tzinfo=UTC)
EXPIRES = datetime(2026, 6, 12, 9, 30, tzinfo=UTC)
NOW = datetime(2026, 6, 12, 9, 15, tzinfo=UTC)
SECRET = "test-signoff-secret"
APPROVER = "operator@example.test"
HASHES = {"execution": "deadbeef"}


def _ticket() -> OrderTicket:
    basket = Basket(
        basket_id="bsk-1",
        trade_date=ISSUED.date(),
        underlying="SX5E",
        legs=(
            BasketLeg(
                instrument_kind="option", side="long", quantity=2,
                underlying="SX5E", tenor_label="3M", delta_band="25d",
            ),
        ),
    )
    return build_ticket(basket)


def _signed() -> SignedTicket:
    ticket = _ticket()
    bh = ticket_binding_hash(ticket)
    return SignedTicket(
        ticket=ticket,
        approval_token=issue_token(
            binding_hash=bh, approver=APPROVER, expires_at=EXPIRES, secret=SECRET
        ),
        approver=APPROVER,
        binding_hash=bh,
        issued_at=ISSUED,
        expires_at=EXPIRES,
    )


class _SpySubmitter:

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def submit(self, order: dict[str, Any]) -> object:
        self.calls.append(order)
        return {"order_id": "OK-1", "status": "Submitted"}


def _mint(seq: int) -> str:
    return f"evt-{seq}"


def test_flag_absent_blocks_default_and_never_calls_the_submit_method() -> None:
    spy = _SpySubmitter()
    sink = LiveSubmitSink(spy)
    log = InMemoryTransmitAuditLog()
    result = transmit(
        _signed(),
        audit_log=log,
        now=NOW,
        config_hashes=HASHES,
        mint_event_id=_mint,
        sink=sink,
        gate=load_transmit_gate({}),
        verify_signoff=lambda s: True,
    )
    assert result.decision is TransmissionDecision.BLOCKED_DEFAULT
    assert spy.calls == [], "the broker submit method must never be invoked with the flag absent"
    assert result.outcome.venue_ack is None


def test_paper_records_but_no_bytes_reach_the_venue() -> None:
    spy = _SpySubmitter()
    sink = LiveSubmitSink(spy)
    log = InMemoryTransmitAuditLog()
    result = transmit(
        _signed(),
        audit_log=log,
        now=NOW,
        config_hashes=HASHES,
        mint_event_id=_mint,
        sink=sink,
        gate=load_transmit_gate({"EXECUTION_TRANSMIT_ENABLED": "paper"}),
        verify_signoff=lambda s: True,
    )
    assert result.decision is TransmissionDecision.SENT_PAPER
    assert spy.calls == [], "paper transmission must never call the live submit method"


def test_only_sent_live_reaches_the_submit_method() -> None:
    spy = _SpySubmitter()
    sink = LiveSubmitSink(spy)
    log = InMemoryTransmitAuditLog()
    result = transmit(
        _signed(),
        audit_log=log,
        now=NOW,
        config_hashes=HASHES,
        mint_event_id=_mint,
        sink=sink,
        gate=load_transmit_gate(
            {"EXECUTION_TRANSMIT_ENABLED": "live", "EXECUTION_SECURITY_REVIEW": "green"}
        ),
        verify_signoff=lambda s: True,
    )
    assert result.decision is TransmissionDecision.SENT_LIVE
    assert len(spy.calls) == 1
    assert spy.calls[0]["binding_hash"] == _signed().binding_hash


def test_live_sink_satisfies_the_order_submitter_port() -> None:
    spy = _SpySubmitter()
    assert isinstance(spy, OrderSubmitter)


def test_transmit_writes_a_stamped_audit_trail() -> None:
    log = InMemoryTransmitAuditLog()
    result = transmit(
        _signed(),
        audit_log=log,
        now=NOW,
        config_hashes=HASHES,
        mint_event_id=_mint,
        gate=load_transmit_gate({"EXECUTION_TRANSMIT_ENABLED": "paper"}),
        verify_signoff=lambda s: True,
    )
    events = [r.event for r in result.audit]
    assert events == ["gate_evaluated", "decision", "transmit_attempt"]
    for record in result.audit:
        assert record.provenance.stamp_hash
