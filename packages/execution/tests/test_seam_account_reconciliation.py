from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from algotrading.execution import Fill, booked_position_set, position_set_from_fills
from algotrading.execution.ledger import InMemoryFillsLedger
from algotrading.infra.contracts import (
    BrokerAccountSnapshot,
    BrokerCashBalance,
    BrokerFill,
    BrokerPosition,
)
from algotrading.infra.risk import (
    RECON_STATUS_BREAK,
    RECON_STATUS_MATCH,
    reconcile_account,
)

AS_OF = datetime(2026, 6, 12, 16, 30, tzinfo=UTC)
BOOK_TS = datetime(2026, 6, 12, 16, 35, tzinfo=UTC)
TRADE_DATE = date(2026, 6, 12)
ACCOUNT = "DUQ574355"
CALL_KEY = "SX5E|OPT|EUREX|EUR|10|o-C-4400|2026-09-18|4400|C"
CONID = 265598


def _broker_snapshot(quantity: float) -> BrokerAccountSnapshot:
    return BrokerAccountSnapshot(
        account_id=ACCOUNT,
        as_of_ts=AS_OF,
        positions=(
            BrokerPosition(
                as_of_ts=AS_OF,
                account_id=ACCOUNT,
                conid=CONID,
                contract_key=CALL_KEY,
                quantity=quantity,
                avg_cost=10.0,
                market_price=10.5,
                market_value=quantity * 105.0,
                currency="EUR",
            ),
        ),
        cash_balances=(
            BrokerCashBalance(
                as_of_ts=AS_OF,
                account_id=ACCOUNT,
                currency="EUR",
                cash_balance=100000.0,
                settled_cash=98000.0,
                net_liquidation=109310.0,
            ),
        ),
        fills=(
            BrokerFill(
                account_id=ACCOUNT,
                execution_id="exec-1",
                conid=CONID,
                contract_key=CALL_KEY,
                side="BUY",
                quantity=quantity,
                price=10.0,
                currency="EUR",
                venue_ts=AS_OF,
                trade_date=TRADE_DATE,
            ),
        ),
    )


def test_real_fill_satisfies_the_book_fill_protocol_and_reconciles_clean(
    make_fill: Callable[..., Fill],
) -> None:
    fills = [
        make_fill(
            fill_id="f-1",
            contract_key=CALL_KEY,
            signed_qty=Decimal("10"),
            broker_contract_id=str(CONID),
            fill_ts=BOOK_TS,
        )
    ]
    position_set = position_set_from_fills(fills, source_ts=BOOK_TS)
    report = reconcile_account(_broker_snapshot(10.0), position_set, book_fills=fills)
    assert [line.status for line in report.position_lines] == [RECON_STATUS_MATCH]
    assert [line.status for line in report.fill_lines] == [RECON_STATUS_MATCH]
    assert report.ok is True


def test_seam_break_when_booked_book_disagrees_with_broker(
    make_fill: Callable[..., Fill],
) -> None:
    fills = [
        make_fill(
            fill_id="f-1",
            contract_key=CALL_KEY,
            signed_qty=Decimal("7"),
            broker_contract_id=str(CONID),
            fill_ts=BOOK_TS,
        )
    ]
    ledger = InMemoryFillsLedger()
    for fill in fills:
        ledger.append(fill)
    position_set = booked_position_set(ledger, source_ts=BOOK_TS)
    report = reconcile_account(
        _broker_snapshot(10.0), position_set, book_fills=ledger.read()
    )
    assert report.position_lines[0].status == RECON_STATUS_BREAK
    assert report.position_lines[0].quantity_diff == pytest.approx(3.0)
    assert report.fill_lines[0].status == RECON_STATUS_BREAK
    assert report.ok is False
