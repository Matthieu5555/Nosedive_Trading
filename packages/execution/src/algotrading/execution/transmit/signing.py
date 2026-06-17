from __future__ import annotations

import hmac
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Protocol, runtime_checkable

from algotrading.infra.orders import OrderTicket, TicketLeg

from .binding import ticket_binding_hash

ENV_SIGNOFF_HMAC_KEY = "EXECUTION_SIGNOFF_SECRET"


class SignoffError(Exception):

    def __init__(self, reason: str, *, field: str, value: object) -> None:
        self.reason = reason
        self.field = field
        self.value = value
        super().__init__(f"{field}={value!r}: {reason}")


@dataclass(frozen=True, slots=True)
class SignedTicket:

    ticket: OrderTicket
    approval_token: str
    approver: str
    binding_hash: str
    issued_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        for name in ("approval_token", "approver", "binding_hash"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise SignoffError("must be a non-empty string", field=name, value=value)
        if self.issued_at.tzinfo is None:
            raise SignoffError("must be timezone-aware", field="issued_at", value=self.issued_at)
        if self.expires_at.tzinfo is None:
            raise SignoffError(
                "must be timezone-aware", field="expires_at", value=self.expires_at
            )
        if self.expires_at <= self.issued_at:
            raise SignoffError(
                "expiry must be strictly after issuance",
                field="expires_at",
                value=(self.issued_at, self.expires_at),
            )


@dataclass(frozen=True, slots=True)
class ApprovalRequest:

    approver: str
    binding_hash: str
    issued_at: datetime
    expires_at: datetime
    summary: tuple[str, ...]


@runtime_checkable
class SignoffChannel(Protocol):

    def deliver(self, request: ApprovalRequest) -> None: ...


def _leg_summary(leg: TicketLeg) -> str:
    side = leg.side.value
    quantity = leg.quantity
    underlying = leg.underlying
    band = leg.delta_band
    tenor = leg.tenor_label
    cell = f" {tenor}/{band}" if band is not None or tenor is not None else ""
    return f"{side} {quantity} {underlying}{cell}"


def render_approval_request(
    ticket: OrderTicket,
    *,
    approver: str,
    issued_at: datetime,
    expires_at: datetime,
) -> ApprovalRequest:
    if not approver.strip():
        raise SignoffError("an approver must be named", field="approver", value=approver)
    return ApprovalRequest(
        approver=approver,
        binding_hash=ticket_binding_hash(ticket),
        issued_at=issued_at,
        expires_at=expires_at,
        summary=tuple(_leg_summary(leg) for leg in ticket.legs),
    )


def _token_message(*, binding_hash: str, approver: str, expires_at: datetime) -> bytes:
    payload = "\x1f".join((binding_hash, approver, expires_at.astimezone(UTC).isoformat()))
    return payload.encode("utf-8")


def issue_token(
    *,
    binding_hash: str,
    approver: str,
    expires_at: datetime,
    secret: str,
) -> str:
    if not secret:
        raise SignoffError("a signing secret is required", field="secret", value=secret)
    message = _token_message(
        binding_hash=binding_hash, approver=approver, expires_at=expires_at
    )
    return hmac.new(secret.encode("utf-8"), message, sha256).hexdigest()


def signoff_token_valid(signed: SignedTicket, *, secret: str | None) -> bool:
    if not secret:
        return False
    expected = issue_token(
        binding_hash=signed.binding_hash,
        approver=signed.approver,
        expires_at=signed.expires_at,
        secret=secret,
    )
    return hmac.compare_digest(signed.approval_token, expected)


def signoff_token_valid_from_environment(
    signed: SignedTicket, env: Mapping[str, str] | None = None
) -> bool:
    source = env if env is not None else os.environ
    return signoff_token_valid(signed, secret=source.get(ENV_SIGNOFF_HMAC_KEY))


def binds_ticket(signed: SignedTicket, ticket: OrderTicket) -> bool:
    expected = ticket_binding_hash(ticket)
    return hmac.compare_digest(signed.binding_hash, expected)


def signoff_unexpired(signed: SignedTicket, now: datetime) -> bool:
    if now.tzinfo is None:
        raise SignoffError("now must be timezone-aware", field="now", value=now)
    return now < signed.expires_at
