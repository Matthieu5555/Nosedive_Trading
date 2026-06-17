from __future__ import annotations

from datetime import UTC, datetime

import pytest
from algotrading.execution.transmit import (
    ApprovalRequest,
    SignedTicket,
    SignoffError,
    issue_token,
    render_approval_request,
    signoff_token_valid,
    signoff_unexpired,
    ticket_binding_hash,
    ticket_binding_payload,
)
from algotrading.infra.contracts import Basket, BasketLeg
from algotrading.infra.orders import OrderTicket, build_ticket

ISSUED = datetime(2026, 6, 12, 9, 0, tzinfo=UTC)
EXPIRES = datetime(2026, 6, 12, 9, 30, tzinfo=UTC)
SECRET = "test-signoff-secret"
APPROVER = "operator@example.test"


def _ticket(*, two_legs: bool = False) -> OrderTicket:
    legs = [
        BasketLeg(
            instrument_kind="option", side="long", quantity=2,
            underlying="SX5E", tenor_label="3M", delta_band="25d",
        )
    ]
    if two_legs:
        legs.append(
            BasketLeg(
                instrument_kind="option", side="short", quantity=-1,
                underlying="SX5E", tenor_label="3M", delta_band="10d",
            )
        )
    basket = Basket(
        basket_id="bsk-1", trade_date=ISSUED.date(), underlying="SX5E", legs=tuple(legs)
    )
    return build_ticket(basket)


def test_binding_hash_is_deterministic_for_the_same_ticket() -> None:
    assert ticket_binding_hash(_ticket()) == ticket_binding_hash(_ticket())


def test_binding_hash_changes_when_a_leg_changes() -> None:
    assert ticket_binding_hash(_ticket()) != ticket_binding_hash(_ticket(two_legs=True))


def test_binding_payload_names_the_material_fields() -> None:
    payload = ticket_binding_payload(_ticket())
    assert set(payload) == {
        "source_basket_id", "trade_date", "underlying", "target_broker",
        "time_in_force", "mode", "legs",
    }
    assert payload["legs"][0]["side"] == "buy"


def test_render_approval_request_carries_the_binding_hash_and_a_legible_summary() -> None:
    request = render_approval_request(
        _ticket(), approver=APPROVER, issued_at=ISSUED, expires_at=EXPIRES
    )
    assert isinstance(request, ApprovalRequest)
    assert request.binding_hash == ticket_binding_hash(_ticket())
    assert request.approver == APPROVER
    assert len(request.summary) == 1
    assert "buy" in request.summary[0]


def test_render_rejects_an_empty_approver() -> None:
    with pytest.raises(SignoffError) as exc:
        render_approval_request(_ticket(), approver="  ", issued_at=ISSUED, expires_at=EXPIRES)
    assert exc.value.field == "approver"


def test_token_round_trip_with_the_right_secret() -> None:
    bh = ticket_binding_hash(_ticket())
    token = issue_token(binding_hash=bh, approver=APPROVER, expires_at=EXPIRES, secret=SECRET)
    signed = SignedTicket(
        ticket=_ticket(), approval_token=token, approver=APPROVER,
        binding_hash=bh, issued_at=ISSUED, expires_at=EXPIRES,
    )
    assert signoff_token_valid(signed, secret=SECRET)


def test_a_garbage_token_does_not_verify() -> None:
    bh = ticket_binding_hash(_ticket())
    signed = SignedTicket(
        ticket=_ticket(), approval_token="garbage", approver=APPROVER,
        binding_hash=bh, issued_at=ISSUED, expires_at=EXPIRES,
    )
    assert not signoff_token_valid(signed, secret=SECRET)


def test_issue_token_requires_a_secret() -> None:
    with pytest.raises(SignoffError):
        issue_token(binding_hash="x", approver=APPROVER, expires_at=EXPIRES, secret="")


def test_signed_ticket_rejects_expiry_before_issuance() -> None:
    with pytest.raises(SignoffError) as exc:
        SignedTicket(
            ticket=_ticket(), approval_token="t", approver=APPROVER,
            binding_hash=ticket_binding_hash(_ticket()),
            issued_at=EXPIRES, expires_at=ISSUED,
        )
    assert exc.value.field == "expires_at"


def test_signed_ticket_rejects_a_naive_expiry() -> None:
    with pytest.raises(SignoffError) as exc:
        SignedTicket(
            ticket=_ticket(), approval_token="t", approver=APPROVER,
            binding_hash=ticket_binding_hash(_ticket()),
            issued_at=ISSUED, expires_at=datetime(2026, 6, 12, 9, 30),
        )
    assert exc.value.field == "expires_at"


def test_signoff_unexpired_rejects_a_naive_now() -> None:
    signed = SignedTicket(
        ticket=_ticket(), approval_token="t", approver=APPROVER,
        binding_hash=ticket_binding_hash(_ticket()), issued_at=ISSUED, expires_at=EXPIRES,
    )
    with pytest.raises(SignoffError) as exc:
        signoff_unexpired(signed, datetime(2026, 6, 12, 9, 15))
    assert exc.value.field == "now"
