from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from algotrading.infra.contracts import (
    BrokerAccountSnapshot,
    BrokerCashBalance,
    BrokerFill,
    BrokerPosition,
)
from algotrading.infra.risk import Position, PositionSet, reconcile_account

_GOLDEN = Path(__file__).parent / "golden" / "account_reconciliation.json"

AS_OF = datetime(2026, 6, 12, 16, 30, tzinfo=UTC)
BOOK_TS = datetime(2026, 6, 12, 16, 35, tzinfo=UTC)
TRADE_DATE = date(2026, 6, 12)
ACCOUNT = "DUQ574355"

CALL_KEY = "SX5E|OPT|EUREX|EUR|10|o-C-4400|2026-09-18|4400|C"
PUT_KEY = "SX5E|OPT|EUREX|EUR|10|o-P-4200|2026-09-18|4200|P"
BROKER_ONLY_KEY = "SX5E|OPT|EUREX|EUR|10|o-C-4600|2026-09-18|4600|C"
BOOK_ONLY_KEY = "SX5E|OPT|EUREX|EUR|10|o-P-4000|2026-09-18|4000|P"

CALL_CONID = 265598
PUT_CONID = 311042
BROKER_ONLY_CONID = 400000
BOOK_ONLY_CONID = 500000


def _broker_position(conid: int, contract_key: str, quantity: float) -> BrokerPosition:
    return BrokerPosition(
        as_of_ts=AS_OF,
        account_id=ACCOUNT,
        conid=conid,
        contract_key=contract_key,
        quantity=quantity,
        avg_cost=12.0,
        market_price=12.5,
        market_value=quantity * 125.0,
        currency="EUR",
    )


def _book_position(contract_key: str, quantity: str, conid: int) -> Position:
    return Position(
        contract_key=contract_key,
        quantity=Decimal(quantity),
        broker_contract_id=str(conid),
    )


def _broker_fill(conid: int, contract_key: str, side: str, quantity: float) -> BrokerFill:
    return BrokerFill(
        account_id=ACCOUNT,
        execution_id=f"exec-{conid}-{side}",
        conid=conid,
        contract_key=contract_key,
        side=side,
        quantity=quantity,
        price=12.4,
        currency="EUR",
        venue_ts=AS_OF,
        trade_date=TRADE_DATE,
    )


class _BookFill:

    def __init__(self, contract_key: str, signed_qty: str, conid: int) -> None:
        self.contract_key = contract_key
        self.signed_qty = Decimal(signed_qty)
        self.broker_contract_id = str(conid)


def build_report() -> object:
    snapshot = BrokerAccountSnapshot(
        account_id=ACCOUNT,
        as_of_ts=AS_OF,
        positions=(
            _broker_position(CALL_CONID, CALL_KEY, 5.0),
            _broker_position(PUT_CONID, PUT_KEY, -4.0),
            _broker_position(BROKER_ONLY_CONID, BROKER_ONLY_KEY, 2.0),
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
            _broker_fill(CALL_CONID, CALL_KEY, "BUY", 5.0),
            _broker_fill(PUT_CONID, PUT_KEY, "SELL", 4.0),
        ),
    )
    book = PositionSet(
        positions=(
            _book_position(CALL_KEY, "5", CALL_CONID),
            _book_position(PUT_KEY, "-3", PUT_CONID),
            _book_position(BOOK_ONLY_KEY, "1", BOOK_ONLY_CONID),
        ),
        source="booked",
        source_ts=BOOK_TS,
    )
    book_fills = (
        _BookFill(CALL_KEY, "5", CALL_CONID),
        _BookFill(PUT_KEY, "-4", PUT_CONID),
    )
    return reconcile_account(snapshot, book, book_fills=book_fills)


def _jsonable(value: object) -> object:
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_jsonable(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def golden_payload() -> dict[str, object]:
    report = build_report()
    body = asdict(report)  # type: ignore[call-overload]
    body.pop("as_of_ts", None)
    body.pop("book_source_ts", None)
    return _jsonable(body)  # type: ignore[return-value]


def test_account_reconciliation_matches_golden() -> None:
    expected = json.loads(_GOLDEN.read_text(encoding="utf-8"))
    assert golden_payload() == expected
