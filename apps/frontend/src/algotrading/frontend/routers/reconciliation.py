from __future__ import annotations

from datetime import UTC, datetime

from algotrading.execution import JsonlFillsLedger, booked_position_set
from algotrading.infra.contracts import (
    BrokerAccountSnapshot,
    BrokerCashBalance,
    BrokerFill,
    BrokerPosition,
)
from algotrading.infra.risk import reconcile_account
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..context import AppContext
from ..deps import BadRequestError, CtxDep
from ..reconciliation_view import reconciliation_report_to_dict

router = APIRouter(prefix="/api/reconciliation", tags=["reconciliation"])

_BOOKING_DIRNAME = "booking"
_FILLS_FILENAME = "fills.jsonl"


def _ledger(ctx: AppContext) -> JsonlFillsLedger:
    return JsonlFillsLedger(ctx.store_root / _BOOKING_DIRNAME / _FILLS_FILENAME)


def _latest_account_id(positions: list[BrokerPosition]) -> str | None:
    if not positions:
        return None
    latest = max(positions, key=lambda row: row.as_of_ts)
    return latest.account_id


def _snapshot_for_account(ctx: AppContext, account_id: str) -> BrokerAccountSnapshot:
    positions = [
        row
        for row in ctx.store.read("broker_positions")
        if isinstance(row, BrokerPosition) and row.account_id == account_id
    ]
    cash = [
        row
        for row in ctx.store.read("broker_cash_balances")
        if isinstance(row, BrokerCashBalance) and row.account_id == account_id
    ]
    fills = [
        row
        for row in ctx.store.read("broker_fills")
        if isinstance(row, BrokerFill) and row.account_id == account_id
    ]
    as_of_candidates = [row.as_of_ts for row in positions] + [row.as_of_ts for row in cash]
    as_of_ts = max(as_of_candidates) if as_of_candidates else datetime.now(UTC)
    latest_positions = tuple(row for row in positions if row.as_of_ts == as_of_ts)
    latest_cash = tuple(row for row in cash if row.as_of_ts == as_of_ts)
    return BrokerAccountSnapshot(
        account_id=account_id,
        as_of_ts=as_of_ts,
        positions=latest_positions,
        cash_balances=latest_cash,
        fills=tuple(fills),
    )


@router.get("")
def get_reconciliation(ctx: CtxDep, account_id: str | None = None) -> JSONResponse:
    resolved_account = account_id
    if resolved_account is None:
        broker_positions = [
            row for row in ctx.store.read("broker_positions") if isinstance(row, BrokerPosition)
        ]
        resolved_account = _latest_account_id(broker_positions)
    if resolved_account is None:
        raise BadRequestError(
            {"error": "no_broker_account", "detail": "no broker_positions captured to reconcile"}
        )
    snapshot = _snapshot_for_account(ctx, resolved_account)
    ledger = _ledger(ctx)
    book_fills = list(ledger.read())
    position_set = booked_position_set(ledger, source_ts=datetime.now(UTC))
    report = reconcile_account(snapshot, position_set, book_fills=book_fills)
    return JSONResponse(reconciliation_report_to_dict(report))
