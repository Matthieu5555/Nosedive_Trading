"""Shared factories for the execution (fills/booking) test suite.

Kept as conftest fixtures rather than an importable ``fixtures`` package: under
``--import-mode=importlib`` a second top-level ``fixtures`` module would collide with the
infra suite's. The factories build *valid-by-construction* fills and stamps so each test
states only the field it is exercising.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from algotrading.core.provenance import ProvenanceStamp, source_ref, stamp
from algotrading.execution import Fill

TRADE_DATE = date(2026, 6, 12)
FILL_TS = datetime(2026, 6, 12, 15, 30, tzinfo=UTC)


@pytest.fixture
def fill_ts() -> datetime:
    """The fixed, timezone-aware timestamp the fixture fills are stamped at."""
    return FILL_TS


@pytest.fixture
def make_stamp() -> Callable[..., ProvenanceStamp]:
    """A factory for a valid provenance stamp pointing at one source contract."""

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
    """A factory for a valid paper fill; override any field via keyword."""

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
