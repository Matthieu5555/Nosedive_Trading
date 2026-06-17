from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from algotrading.execution.transmit import (
    SignedTicket,
    TransmissionDecision,
    binds_ticket,
    decide_transmission,
    issue_token,
    load_transmit_gate,
    signoff_token_valid,
    ticket_binding_hash,
)
from algotrading.execution.transmit.gate import (
    ENV_SECURITY_REVIEW,
    ENV_TRANSMIT_ENABLED,
    GateUnparseable,
)
from algotrading.infra.contracts import Basket, BasketLeg
from algotrading.infra.orders import Limit, OrderTicket, Side, TicketLeg, build_ticket

ISSUED = datetime(2026, 6, 12, 9, 0, tzinfo=UTC)
EXPIRES = datetime(2026, 6, 12, 9, 30, tzinfo=UTC)
NOW_OK = datetime(2026, 6, 12, 9, 15, tzinfo=UTC)
SECRET = "test-signoff-secret"
APPROVER = "operator@example.test"


def _ticket() -> OrderTicket:
    basket = Basket(
        basket_id="bsk-1",
        trade_date=ISSUED.date(),
        underlying="SX5E",
        legs=(
            BasketLeg(
                instrument_kind="option",
                side="long",
                quantity=2,
                underlying="SX5E",
                tenor_label="3M",
                delta_band="25d",
            ),
        ),
    )
    return build_ticket(basket)


def _signed(
    ticket: OrderTicket | None = None,
    *,
    binding_hash: str | None = None,
    token: str | None = None,
    expires_at: datetime = EXPIRES,
) -> SignedTicket:
    ticket = ticket if ticket is not None else _ticket()
    bh = binding_hash if binding_hash is not None else ticket_binding_hash(ticket)
    tok = (
        token
        if token is not None
        else issue_token(
            binding_hash=bh, approver=APPROVER, expires_at=expires_at, secret=SECRET
        )
    )
    return SignedTicket(
        ticket=ticket,
        approval_token=tok,
        approver=APPROVER,
        binding_hash=bh,
        issued_at=ISSUED,
        expires_at=expires_at,
    )


def _verifier(valid: bool) -> Callable[[SignedTicket], bool]:
    return lambda signed: valid


# The decision table is the independent oracle: it is hand-written here, never derived
# from decide_transmission. Each row asserts (flag, signoff, review) -> a named decision.
# signoff "mismatched" is modelled by a binding_hash that does not match the ticket;
# every other signoff state is injected through verify_signoff so the cell is deterministic.

_FLAGS = {
    "absent": {},
    "paper": {ENV_TRANSMIT_ENABLED: "paper"},
    "live": {ENV_TRANSMIT_ENABLED: "live"},
}
_REVIEW = {"green": {ENV_SECURITY_REVIEW: "green"}, "not-recorded": {}}

_TABLE = {
    ("absent", "valid", "green"): TransmissionDecision.BLOCKED_DEFAULT,
    ("absent", "valid", "not-recorded"): TransmissionDecision.BLOCKED_DEFAULT,
    ("absent", "missing", "green"): TransmissionDecision.BLOCKED_DEFAULT,
    ("absent", "missing", "not-recorded"): TransmissionDecision.BLOCKED_DEFAULT,
    ("absent", "expired", "green"): TransmissionDecision.BLOCKED_DEFAULT,
    ("absent", "expired", "not-recorded"): TransmissionDecision.BLOCKED_DEFAULT,
    ("absent", "mismatched-ticket", "green"): TransmissionDecision.BLOCKED_DEFAULT,
    ("absent", "mismatched-ticket", "not-recorded"): TransmissionDecision.BLOCKED_DEFAULT,
    ("paper", "valid", "green"): TransmissionDecision.SENT_PAPER,
    ("paper", "valid", "not-recorded"): TransmissionDecision.SENT_PAPER,
    ("paper", "missing", "green"): TransmissionDecision.BLOCKED_NO_SIGNOFF,
    ("paper", "missing", "not-recorded"): TransmissionDecision.BLOCKED_NO_SIGNOFF,
    ("paper", "expired", "green"): TransmissionDecision.BLOCKED_EXPIRED,
    ("paper", "expired", "not-recorded"): TransmissionDecision.BLOCKED_EXPIRED,
    ("paper", "mismatched-ticket", "green"): TransmissionDecision.BLOCKED_TICKET_MISMATCH,
    ("paper", "mismatched-ticket", "not-recorded"): TransmissionDecision.BLOCKED_TICKET_MISMATCH,
    ("live", "valid", "green"): TransmissionDecision.SENT_LIVE,
    ("live", "valid", "not-recorded"): TransmissionDecision.BLOCKED_GATE_OFF,
    ("live", "missing", "green"): TransmissionDecision.BLOCKED_NO_SIGNOFF,
    ("live", "missing", "not-recorded"): TransmissionDecision.BLOCKED_NO_SIGNOFF,
    ("live", "expired", "green"): TransmissionDecision.BLOCKED_EXPIRED,
    ("live", "expired", "not-recorded"): TransmissionDecision.BLOCKED_EXPIRED,
    ("live", "mismatched-ticket", "green"): TransmissionDecision.BLOCKED_TICKET_MISMATCH,
    ("live", "mismatched-ticket", "not-recorded"): TransmissionDecision.BLOCKED_TICKET_MISMATCH,
}


@pytest.mark.parametrize(("flag", "signoff", "review"), list(_TABLE))
def test_decision_table_full_cross_product(flag: str, signoff: str, review: str) -> None:
    env = {**_FLAGS[flag], **_REVIEW[review]}
    gate = load_transmit_gate(env)

    if signoff == "mismatched-ticket":
        signed = _signed(binding_hash="0" * 64)
        verify = _verifier(True)
        now = NOW_OK
    elif signoff == "expired":
        signed = _signed()
        verify = _verifier(True)
        now = EXPIRES + timedelta(seconds=1)
    elif signoff == "missing":
        signed = _signed()
        verify = _verifier(False)
        now = NOW_OK
    else:
        signed = _signed()
        verify = _verifier(True)
        now = NOW_OK

    decision = decide_transmission(signed, gate, now, verify_signoff=verify)
    assert decision is _TABLE[(flag, signoff, review)], (flag, signoff, review)


def test_the_table_covers_every_cell_of_the_cross_product() -> None:
    expected = {
        (flag, signoff, review)
        for flag in ("absent", "paper", "live")
        for signoff in ("valid", "missing", "expired", "mismatched-ticket")
        for review in ("green", "not-recorded")
    }
    assert set(_TABLE) == expected


def test_unparseable_flag_is_blocked_default() -> None:
    gate = load_transmit_gate({ENV_TRANSMIT_ENABLED: "maybe"})
    assert isinstance(gate, GateUnparseable)
    decision = decide_transmission(_signed(), gate, NOW_OK, verify_signoff=_verifier(True))
    assert decision is TransmissionDecision.BLOCKED_DEFAULT


def test_token_binds_exact_ticket_symbol_perturbation() -> None:
    base = _ticket()
    signed = _signed(base)
    perturbed = OrderTicket(
        source_basket_id=base.source_basket_id,
        trade_date=base.trade_date,
        underlying="SPX",
        target_broker=base.target_broker,
        time_in_force=base.time_in_force,
        legs=(
            TicketLeg(
                instrument_kind="option",
                underlying="SPX",
                side=Side.BUY,
                quantity=2,
                price_spec=base.legs[0].price_spec,
                tenor_label="3M",
                delta_band="25d",
            ),
        ),
    )
    presented = SignedTicket(
        ticket=perturbed,
        approval_token=signed.approval_token,
        approver=signed.approver,
        binding_hash=signed.binding_hash,
        issued_at=signed.issued_at,
        expires_at=signed.expires_at,
    )
    assert not binds_ticket(presented, perturbed)
    assert (
        decide_transmission(
            presented, load_transmit_gate(_FLAGS["live"]), NOW_OK, verify_signoff=_verifier(True)
        )
        is TransmissionDecision.BLOCKED_TICKET_MISMATCH
    )


@pytest.mark.parametrize(
    ("field", "make"),
    [
        ("side", lambda leg: TicketLeg(
            instrument_kind="option", underlying="SX5E", side=Side.SELL,
            quantity=leg.quantity, price_spec=leg.price_spec,
            tenor_label="3M", delta_band="25d")),
        ("qty", lambda leg: TicketLeg(
            instrument_kind="option", underlying="SX5E", side=Side.BUY,
            quantity=7, price_spec=leg.price_spec,
            tenor_label="3M", delta_band="25d")),
        ("limit", lambda leg: TicketLeg(
            instrument_kind="option", underlying="SX5E", side=Side.BUY,
            quantity=leg.quantity, price_spec=Limit(99.0),
            tenor_label="3M", delta_band="25d")),
    ],
)
def test_perturbing_each_material_field_breaks_the_binding(
    field: str, make: Callable[[TicketLeg], TicketLeg]
) -> None:
    base = _ticket()
    signed = _signed(base)
    perturbed = OrderTicket(
        source_basket_id=base.source_basket_id,
        trade_date=base.trade_date,
        underlying=base.underlying,
        target_broker=base.target_broker,
        time_in_force=base.time_in_force,
        legs=(make(base.legs[0]),),
    )
    presented = SignedTicket(
        ticket=perturbed,
        approval_token=signed.approval_token,
        approver=signed.approver,
        binding_hash=signed.binding_hash,
        issued_at=signed.issued_at,
        expires_at=signed.expires_at,
    )
    assert not binds_ticket(presented, perturbed), field


@pytest.mark.parametrize(
    ("offset_seconds", "expected"),
    [
        (-1, TransmissionDecision.SENT_PAPER),
        (0, TransmissionDecision.BLOCKED_EXPIRED),
        (1, TransmissionDecision.BLOCKED_EXPIRED),
    ],
)
def test_expiry_boundary_on_the_second_is_rejected(
    offset_seconds: int, expected: TransmissionDecision
) -> None:
    signed = _signed()
    now = EXPIRES + timedelta(seconds=offset_seconds)
    decision = decide_transmission(
        signed, load_transmit_gate(_FLAGS["paper"]), now, verify_signoff=_verifier(True)
    )
    assert decision is expected


def test_real_token_verification_round_trip() -> None:
    signed = _signed()
    assert signoff_token_valid(signed, secret=SECRET)
    assert not signoff_token_valid(signed, secret="wrong-secret")
    assert not signoff_token_valid(signed, secret=None)


def test_a_token_for_one_ticket_does_not_verify_for_another() -> None:
    other = OrderTicket(
        source_basket_id="bsk-1",
        trade_date=ISSUED.date(),
        underlying="SX5E",
        target_broker=_ticket().target_broker,
        time_in_force=_ticket().time_in_force,
        legs=(
            TicketLeg(
                instrument_kind="option", underlying="SX5E", side=Side.BUY,
                quantity=5, price_spec=_ticket().legs[0].price_spec,
                tenor_label="3M", delta_band="25d",
            ),
        ),
    )
    signed = _signed()
    presented = SignedTicket(
        ticket=other,
        approval_token=signed.approval_token,
        approver=signed.approver,
        binding_hash=signed.binding_hash,
        issued_at=signed.issued_at,
        expires_at=signed.expires_at,
    )
    assert not binds_ticket(presented, other)
