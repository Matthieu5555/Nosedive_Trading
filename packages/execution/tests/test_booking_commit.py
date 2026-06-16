from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from algotrading.execution import (
    BookingBlocked,
    BookingCommitted,
    Fill,
    InMemoryBookingAuditLog,
    InMemoryFillsLedger,
    ResolvedLeg,
    book,
    booked_position_set,
)
from algotrading.execution.booking import (
    ABSENT_PASSWORD,
    MALFORMED_GATE_CONFIG,
    UNCONFIGURED_GATE,
    UNRESOLVABLE_LEG,
    WRONG_PASSWORD,
    ConcretizationError,
    verify_password,
)
from algotrading.execution.booking.password_gate import ENV_GATE_HASH, ENV_GATE_SALT
from algotrading.infra.orders import (
    Market,
    OrderTicket,
    Side,
    TargetBroker,
    TicketLeg,
    TimeInForce,
)

NOW = datetime(2026, 6, 12, 16, 0, tzinfo=UTC)
BOOKING_PASSWORD = "open-sesame"
CHAIN_MID = 12.0


class _SpyLedger:

    def __init__(self) -> None:
        self._inner = InMemoryFillsLedger()
        self.append_calls = 0
        self.append_many_calls = 0

    def append(self, fill: Fill) -> None:
        self.append_calls += 1
        self._inner.append(fill)

    def append_many(self, fills: object) -> None:
        self.append_many_calls += 1
        self._inner.append_many(fills)  # type: ignore[arg-type]

    def read(
        self, *, trade_date: date | None = None, underlying: str | None = None
    ) -> tuple[Fill, ...]:
        return self._inner.read(trade_date=trade_date, underlying=underlying)

    @property
    def write_invoked(self) -> bool:
        return self.append_calls > 0 or self.append_many_calls > 0


def _book(
    ticket: OrderTicket,
    password: str,
    *,
    ledger: object,
    audit_log: object,
    resolver: Callable[..., ResolvedLeg],
    chain: dict[str, float],
    verify_gate: Callable[[str], object],
    booking_id: str = "bkg-1",
) -> object:
    return book(
        ticket,
        password,
        ledger=ledger,  # type: ignore[arg-type]
        audit_log=audit_log,  # type: ignore[arg-type]
        resolver=resolver,
        chain=chain,
        now=NOW,
        booking_id=booking_id,
        config_hashes={"execution": "deadbeef"},
        mint_fill_id=lambda index: f"{booking_id}-fill-{index}",
        verify_gate=verify_gate,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(
    ("password", "env_override", "expected_reason"),
    [
        ("not-the-password", None, WRONG_PASSWORD),
        ("", None, ABSENT_PASSWORD),
        (BOOKING_PASSWORD, {}, UNCONFIGURED_GATE),
        (
            BOOKING_PASSWORD,
            {ENV_GATE_SALT: "not-hex!!", ENV_GATE_HASH: "also-not-hex"},
            MALFORMED_GATE_CONFIG,
        ),
    ],
)
def test_a_failed_gate_blocks_and_never_writes_the_ledger(
    make_ticket: Callable[..., OrderTicket],
    reference_resolver: Callable[..., ResolvedLeg],
    chain: dict[str, float],
    gate_env: dict[str, str],
    password: str,
    env_override: dict[str, str] | None,
    expected_reason: str,
) -> None:
    ticket = make_ticket()
    ledger = _SpyLedger()
    audit_log = InMemoryBookingAuditLog()
    env = gate_env if env_override is None else env_override
    gate = lambda pw: verify_password(pw, env)  # noqa: E731

    result = _book(
        ticket, password, ledger=ledger, audit_log=audit_log,
        resolver=reference_resolver, chain=chain, verify_gate=gate,
    )

    assert isinstance(result, BookingBlocked)
    assert result.reason == expected_reason
    assert ledger.write_invoked is False
    assert ledger.read() == ()
    records = audit_log.read()
    assert len(records) == 1
    assert records[0].decision == "block"
    assert records[0].block_reason == expected_reason
    assert records[0].fill_ids == ()


def test_a_verified_commit_writes_signed_paper_fills_once_with_lineage(
    make_ticket: Callable[..., OrderTicket],
    reference_resolver: Callable[..., ResolvedLeg],
    chain: dict[str, float],
    verify_gate: Callable[[str], object],
) -> None:
    ticket = make_ticket(basket_id="bsk-42", two_legs=True)
    ledger = InMemoryFillsLedger()
    audit_log = InMemoryBookingAuditLog()

    result = _book(
        ticket, BOOKING_PASSWORD, ledger=ledger, audit_log=audit_log,
        resolver=reference_resolver, chain=chain, verify_gate=verify_gate, booking_id="bkg-42",
    )

    assert isinstance(result, BookingCommitted)
    fills = result.fills
    assert len(fills) == 2
    assert [f.signed_qty for f in fills] == [Decimal("2"), Decimal("-1")]
    assert {f.price for f in fills} == {CHAIN_MID}
    assert all(f.booking_id == "bkg-42" for f in fills)
    assert all(f.source_basket_id == "bsk-42" for f in fills)
    assert all(f.mode == "paper" for f in fills)
    persisted = ledger.read()
    assert {f.fill_id for f in persisted} == {"bkg-42-fill-0", "bkg-42-fill-1"}
    records = audit_log.read()
    assert len(records) == 1
    assert records[0].decision == "commit"
    assert records[0].fill_ids == ("bkg-42-fill-0", "bkg-42-fill-1")
    assert records[0].block_reason is None


def test_the_booked_fills_fold_into_a_position_keyed_by_concrete_contract(
    make_ticket: Callable[..., OrderTicket],
    reference_resolver: Callable[..., ResolvedLeg],
    chain: dict[str, float],
    verify_gate: Callable[[str], object],
) -> None:
    ticket = make_ticket(basket_id="bsk-1")
    ledger = InMemoryFillsLedger()
    _book(
        ticket, BOOKING_PASSWORD, ledger=ledger, audit_log=InMemoryBookingAuditLog(),
        resolver=reference_resolver, chain=chain, verify_gate=verify_gate,
    )
    book_set = booked_position_set(ledger, source_ts=NOW)
    assert len(book_set.positions) == 1
    (pos,) = book_set.positions
    assert ticket.trade_date.isoformat() in pos.contract_key
    assert pos.quantity == Decimal("2")


def test_partial_fills_on_one_contract_accumulate_without_loss(
    reference_resolver: Callable[..., ResolvedLeg],
    chain: dict[str, float],
    verify_gate: Callable[[str], object],
) -> None:
    leg = TicketLeg(
        instrument_kind="option",
        underlying="SX5E",
        side=Side.BUY,
        quantity=3.0,
        price_spec=Market(),
        tenor_label="3M",
        delta_band="25d",
    )
    ticket = OrderTicket(
        source_basket_id="bsk-1",
        trade_date=date(2026, 6, 12),
        underlying="SX5E",
        target_broker=TargetBroker.IBKR,
        time_in_force=TimeInForce.DAY,
        legs=(leg,),
    )
    ledger = InMemoryFillsLedger()
    audit_log = InMemoryBookingAuditLog()

    def partial_resolver(_leg: TicketLeg, *, as_of: date, chain: object) -> ResolvedLeg:
        return ResolvedLeg(
            contract_key="SX5E|OPT|EUREX|EUR|10|c25d|2026-09-18|5000|C",
            price=chain["mid"],  # type: ignore[index]
            signed_qty=Decimal("1"),
        )

    for n in (1, 2, 3):
        _book(
            ticket, BOOKING_PASSWORD, ledger=ledger, audit_log=audit_log,
            resolver=partial_resolver, chain=chain, verify_gate=verify_gate, booking_id=f"bkg-{n}",
        )
    book_set = booked_position_set(ledger, source_ts=NOW)
    (pos,) = book_set.positions
    assert pos.quantity == Decimal("3")
    assert len(ledger.read()) == 3


def test_an_unresolvable_leg_fails_closed_with_no_fill_written(
    make_ticket: Callable[..., OrderTicket],
    chain: dict[str, float],
    verify_gate: Callable[[str], object],
) -> None:
    ticket = make_ticket()
    ledger = _SpyLedger()
    audit_log = InMemoryBookingAuditLog()

    def failing_resolver(leg: TicketLeg, *, as_of: date, chain: object) -> ResolvedLeg:
        raise ConcretizationError(
            "no contract matches the grid cell as-of the booking date",
            field="delta_band",
            value=leg.delta_band,
        )

    result = _book(
        ticket, BOOKING_PASSWORD, ledger=ledger, audit_log=audit_log,
        resolver=failing_resolver, chain=chain, verify_gate=verify_gate,
    )

    assert isinstance(result, BookingBlocked)
    assert result.reason == UNRESOLVABLE_LEG
    assert ledger.write_invoked is False
    records = audit_log.read()
    assert len(records) == 1
    assert records[0].decision == "block"
    assert records[0].block_reason == UNRESOLVABLE_LEG


def test_the_booking_module_imports_no_broker_or_order_submit_symbol() -> None:
    import importlib
    import pkgutil

    import algotrading.execution.booking as booking

    forbidden = (
        "transmit",
        "place_order",
        "submit_order",
        "send_order",
        "broker_client",
        "credential",
        "oauth",
        "api_key",
    )
    for info in pkgutil.walk_packages(booking.__path__, prefix="algotrading.execution.booking."):
        module = importlib.import_module(info.name)
        offenders = {
            name
            for name in dir(module)
            if not name.startswith("_") and any(tok in name.lower() for tok in forbidden)
        }
        assert offenders == set(), f"{info.name} exposes a forbidden symbol: {offenders}"
