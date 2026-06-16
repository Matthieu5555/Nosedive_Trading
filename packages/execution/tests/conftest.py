from __future__ import annotations

import secrets
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from algotrading.core.provenance import ProvenanceStamp, source_ref, stamp
from algotrading.execution import (
    Fill,
    ResolvedLeg,
    hash_password,
    signed_quantity_for,
    verify_password,
)
from algotrading.execution.booking.password_gate import ENV_GATE_HASH, ENV_GATE_SALT
from algotrading.infra.contracts import Basket, BasketLeg
from algotrading.infra.orders import OrderTicket, TicketLeg, build_ticket

TRADE_DATE = date(2026, 6, 12)
FILL_TS = datetime(2026, 6, 12, 15, 30, tzinfo=UTC)

BOOKING_PASSWORD = "open-sesame"


@pytest.fixture
def fill_ts() -> datetime:
    return FILL_TS


@pytest.fixture
def make_stamp() -> Callable[..., ProvenanceStamp]:

    def _make(contract_key: str = "SX5E|OPT|C|4400") -> ProvenanceStamp:
        return stamp(
            calc_ts=FILL_TS,
            code_version="algotrading-execution/test",
            config_hashes={"execution": "deadbeef"},
            source_records=(source_ref("order_tickets", "bsk-1", contract_key),),
            source_timestamps=(FILL_TS,),
        )

    return _make


@pytest.fixture
def make_fill(make_stamp: Callable[..., ProvenanceStamp]) -> Callable[..., Fill]:

    def _make(
        *,
        fill_id: str = "fill-1",
        booking_id: str = "bkg-1",
        source_basket_id: str = "bsk-1",
        trade_date: date = TRADE_DATE,
        underlying: str = "SX5E",
        contract_key: str = "SX5E|OPT|C|4400",
        signed_qty: Decimal = Decimal("3"),
        price: float = 12.5,
        fill_ts: datetime = FILL_TS,
        mode: str = "paper",
        broker_contract_id: str | None = None,
    ) -> Fill:
        return Fill(
            fill_id=fill_id,
            booking_id=booking_id,
            source_basket_id=source_basket_id,
            trade_date=trade_date,
            underlying=underlying,
            contract_key=contract_key,
            signed_qty=signed_qty,
            price=price,
            fill_ts=fill_ts,
            provenance=make_stamp(contract_key),
            mode=mode,
            broker_contract_id=broker_contract_id,
        )

    return _make


CHAIN_MID = 12.0


@pytest.fixture
def gate_env() -> dict[str, str]:
    salt = secrets.token_bytes(16)
    return {ENV_GATE_SALT: salt.hex(), ENV_GATE_HASH: hash_password(BOOKING_PASSWORD, salt)}


@pytest.fixture
def verify_gate(gate_env: dict[str, str]) -> Callable[[str], object]:
    return lambda password: verify_password(password, gate_env)


@pytest.fixture
def make_ticket() -> Callable[..., OrderTicket]:

    def _make(
        *,
        basket_id: str = "bsk-1",
        trade_date: date = TRADE_DATE,
        two_legs: bool = False,
    ) -> OrderTicket:
        legs = [
            BasketLeg(
                instrument_kind="option",
                side="long",
                quantity=2,
                underlying="SX5E",
                tenor_label="3M",
                delta_band="25d",
            )
        ]
        if two_legs:
            legs.append(
                BasketLeg(
                    instrument_kind="option",
                    side="short",
                    quantity=-1,
                    underlying="SX5E",
                    tenor_label="3M",
                    delta_band="10d",
                )
            )
        basket = Basket(
            basket_id=basket_id, trade_date=trade_date, underlying="SX5E", legs=tuple(legs)
        )
        return build_ticket(basket)

    return _make


@pytest.fixture
def reference_resolver() -> Callable[..., ResolvedLeg]:

    def _resolve(leg: TicketLeg, *, as_of: date, chain: object) -> ResolvedLeg:
        mid = chain["mid"]  # type: ignore[index]
        contract_key = (
            f"SX5E|OPT|EUREX|EUR|10|c{leg.delta_band}|{as_of.isoformat()}|5000|C"
        )
        return ResolvedLeg(
            contract_key=contract_key, price=mid, signed_qty=signed_quantity_for(leg)
        )

    return _resolve


@pytest.fixture
def chain() -> dict[str, float]:
    return {"mid": CHAIN_MID}


@pytest.fixture
def mint_fill_id() -> Callable[[int], str]:
    return lambda index: f"fill-{index}"
