from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from algotrading.infra.contracts import (
    BrokerAccountSnapshot,
    BrokerCashBalance,
    BrokerFill,
    BrokerPosition,
)
from algotrading.infra.risk import (
    ACCOUNT_RECON_TOLERANCE_VERSION,
    DEFAULT_ACCOUNT_RECON_TOLERANCE,
    RECON_STATUS_BOOK_ONLY,
    RECON_STATUS_BREAK,
    RECON_STATUS_BROKER_ONLY,
    RECON_STATUS_MATCH,
    AccountReconciliationTolerance,
    Position,
    PositionSet,
    reconcile_account,
)

AS_OF = datetime(2026, 6, 12, 16, 30, tzinfo=UTC)
BOOK_TS = datetime(2026, 6, 12, 16, 35, tzinfo=UTC)
TRADE_DATE = date(2026, 6, 12)
ACCOUNT = "DUQ574355"

CALL_KEY = "SX5E|OPT|EUREX|EUR|10|o-C-4400|2026-09-18|4400|C"
PUT_KEY = "SX5E|OPT|EUREX|EUR|10|o-P-4200|2026-09-18|4200|P"
CALL_CONID = 265598
PUT_CONID = 311042


def _broker_position(conid: int, contract_key: str, quantity: float) -> BrokerPosition:
    return BrokerPosition(
        as_of_ts=AS_OF,
        account_id=ACCOUNT,
        conid=conid,
        contract_key=contract_key,
        quantity=quantity,
        avg_cost=10.0,
        market_price=10.5,
        market_value=quantity * 10.5 * 10,
        currency="EUR",
    )


def _book_position(contract_key: str, quantity: str, conid: int | None) -> Position:
    return Position(
        contract_key=contract_key,
        quantity=Decimal(quantity),
        broker_contract_id=None if conid is None else str(conid),
    )


def _book_set(*positions: Position) -> PositionSet:
    return PositionSet(positions=positions, source="booked", source_ts=BOOK_TS)


def _broker_fill(conid: int, contract_key: str, side: str, quantity: float) -> BrokerFill:
    return BrokerFill(
        account_id=ACCOUNT,
        execution_id=f"exec-{conid}-{side}-{quantity}",
        conid=conid,
        contract_key=contract_key,
        side=side,
        quantity=quantity,
        price=10.0,
        currency="EUR",
        venue_ts=AS_OF,
        trade_date=TRADE_DATE,
    )


class _StubBookFill:

    def __init__(self, contract_key: str, signed_qty: str, conid: int | None) -> None:
        self.contract_key = contract_key
        self.signed_qty = Decimal(signed_qty)
        self.broker_contract_id = None if conid is None else str(conid)


def _snapshot(
    positions: tuple[BrokerPosition, ...] = (),
    cash: tuple[BrokerCashBalance, ...] = (),
    fills: tuple[BrokerFill, ...] = (),
) -> BrokerAccountSnapshot:
    return BrokerAccountSnapshot(
        account_id=ACCOUNT,
        as_of_ts=AS_OF,
        positions=positions,
        cash_balances=cash,
        fills=fills,
    )


def test_matched_position_on_conid_join_is_a_match() -> None:
    snapshot = _snapshot(positions=(_broker_position(CALL_CONID, CALL_KEY, 10.0),))
    book = _book_set(_book_position(CALL_KEY, "10", CALL_CONID))
    report = reconcile_account(snapshot, book)
    assert len(report.position_lines) == 1
    line = report.position_lines[0]
    assert line.status == RECON_STATUS_MATCH
    assert line.join_key == str(CALL_CONID)
    assert line.quantity_diff == pytest.approx(0.0)
    assert report.position_counts.match == 1
    assert report.ok is True


def test_signed_quantity_disagreement_is_a_break() -> None:
    snapshot = _snapshot(positions=(_broker_position(CALL_CONID, CALL_KEY, 10.0),))
    book = _book_set(_book_position(CALL_KEY, "7", CALL_CONID))
    report = reconcile_account(snapshot, book)
    line = report.position_lines[0]
    assert line.status == RECON_STATUS_BREAK
    assert line.quantity_diff == pytest.approx(3.0)
    assert report.position_counts.breaks == 1
    assert report.ok is False


def test_sign_convention_long_vs_short_is_a_break() -> None:
    snapshot = _snapshot(positions=(_broker_position(CALL_CONID, CALL_KEY, 5.0),))
    book = _book_set(_book_position(CALL_KEY, "-5", CALL_CONID))
    report = reconcile_account(snapshot, book)
    line = report.position_lines[0]
    assert line.status == RECON_STATUS_BREAK
    assert line.quantity_diff == pytest.approx(10.0)


def test_broker_only_and_book_only_positions_are_classified() -> None:
    snapshot = _snapshot(positions=(_broker_position(CALL_CONID, CALL_KEY, 10.0),))
    book = _book_set(_book_position(PUT_KEY, "4", PUT_CONID))
    report = reconcile_account(snapshot, book)
    by_status = {line.status for line in report.position_lines}
    assert by_status == {RECON_STATUS_BROKER_ONLY, RECON_STATUS_BOOK_ONLY}
    assert report.position_counts.broker_only == 1
    assert report.position_counts.book_only == 1
    assert report.ok is False


def test_position_join_falls_back_to_contract_key_when_book_has_no_broker_id() -> None:
    snapshot = _snapshot(positions=(_broker_position(CALL_CONID, CALL_KEY, 10.0),))
    book = _book_set(_book_position(CALL_KEY, "10", None))
    report = reconcile_account(snapshot, book)
    assert len(report.position_lines) == 1
    assert report.position_lines[0].status == RECON_STATUS_MATCH


def test_tolerance_boundary_exact_diff_is_a_match() -> None:
    tol = AccountReconciliationTolerance(version="t", quantity_abs=0.5, cash_abs=1e-2)
    snapshot = _snapshot(positions=(_broker_position(CALL_CONID, CALL_KEY, 10.5),))
    book = _book_set(_book_position(CALL_KEY, "10", CALL_CONID))
    report = reconcile_account(snapshot, book, tolerance=tol)
    assert report.position_lines[0].quantity_diff == pytest.approx(0.5)
    assert report.position_lines[0].status == RECON_STATUS_MATCH


def test_tolerance_boundary_just_over_is_a_break() -> None:
    tol = AccountReconciliationTolerance(version="t", quantity_abs=0.5, cash_abs=1e-2)
    snapshot = _snapshot(positions=(_broker_position(CALL_CONID, CALL_KEY, 10.51),))
    book = _book_set(_book_position(CALL_KEY, "10", CALL_CONID))
    report = reconcile_account(snapshot, book, tolerance=tol)
    assert report.position_lines[0].status == RECON_STATUS_BREAK


def test_empty_snapshot_and_empty_book_reconciles_clean() -> None:
    report = reconcile_account(_snapshot(), _book_set())
    assert report.position_lines == ()
    assert report.cash_lines == ()
    assert report.fill_lines == ()
    assert report.ok is True


def test_cash_balances_are_surfaced_as_broker_only() -> None:
    cash = (
        BrokerCashBalance(
            as_of_ts=AS_OF,
            account_id=ACCOUNT,
            currency="EUR",
            cash_balance=100000.0,
            settled_cash=98000.0,
            net_liquidation=109310.0,
        ),
    )
    report = reconcile_account(_snapshot(cash=cash), _book_set())
    assert len(report.cash_lines) == 1
    line = report.cash_lines[0]
    assert line.currency == "EUR"
    assert line.broker_cash_balance == pytest.approx(100000.0)
    assert line.status == RECON_STATUS_BROKER_ONLY
    assert report.ok is True


def test_non_finite_cash_is_a_break() -> None:
    cash = (
        BrokerCashBalance(
            as_of_ts=AS_OF,
            account_id=ACCOUNT,
            currency="EUR",
            cash_balance=float("nan"),
            settled_cash=0.0,
            net_liquidation=0.0,
        ),
    )
    report = reconcile_account(_snapshot(cash=cash), _book_set())
    assert report.cash_lines[0].status == RECON_STATUS_BREAK


def test_fills_match_on_conid_with_signed_quantity() -> None:
    snapshot = _snapshot(fills=(_broker_fill(CALL_CONID, CALL_KEY, "BUY", 10.0),))
    book_fills = (_StubBookFill(CALL_KEY, "10", CALL_CONID),)
    report = reconcile_account(snapshot, _book_set(), book_fills=book_fills)
    assert len(report.fill_lines) == 1
    line = report.fill_lines[0]
    assert line.status == RECON_STATUS_MATCH
    assert line.broker_signed_quantity == pytest.approx(10.0)
    assert line.book_signed_quantity == pytest.approx(10.0)


def test_broker_sell_fill_is_negative_signed_quantity() -> None:
    snapshot = _snapshot(fills=(_broker_fill(PUT_CONID, PUT_KEY, "SELL", 4.0),))
    book_fills = (_StubBookFill(PUT_KEY, "-4", PUT_CONID),)
    report = reconcile_account(snapshot, _book_set(), book_fills=book_fills)
    line = report.fill_lines[0]
    assert line.broker_signed_quantity == pytest.approx(-4.0)
    assert line.status == RECON_STATUS_MATCH


def test_fill_present_on_one_side_only() -> None:
    snapshot = _snapshot(fills=(_broker_fill(CALL_CONID, CALL_KEY, "BUY", 10.0),))
    book_fills = (_StubBookFill(PUT_KEY, "-4", PUT_CONID),)
    report = reconcile_account(snapshot, _book_set(), book_fills=book_fills)
    statuses = {line.status for line in report.fill_lines}
    assert statuses == {RECON_STATUS_BROKER_ONLY, RECON_STATUS_BOOK_ONLY}
    assert report.fill_counts.broker_only == 1
    assert report.fill_counts.book_only == 1


def test_multiple_broker_fills_on_one_conid_net_before_compare() -> None:
    snapshot = _snapshot(
        fills=(
            _broker_fill(CALL_CONID, CALL_KEY, "BUY", 6.0),
            _broker_fill(CALL_CONID, CALL_KEY, "BUY", 4.0),
        )
    )
    book_fills = (_StubBookFill(CALL_KEY, "10", CALL_CONID),)
    report = reconcile_account(snapshot, _book_set(), book_fills=book_fills)
    assert len(report.fill_lines) == 1
    assert report.fill_lines[0].broker_signed_quantity == pytest.approx(10.0)
    assert report.fill_lines[0].status == RECON_STATUS_MATCH


def test_default_tolerance_version_threads_through() -> None:
    snapshot = _snapshot(positions=(_broker_position(CALL_CONID, CALL_KEY, 10.0),))
    book = _book_set(_book_position(CALL_KEY, "10", CALL_CONID))
    report = reconcile_account(snapshot, book)
    assert report.threshold_version == ACCOUNT_RECON_TOLERANCE_VERSION
    assert report.position_lines[0].threshold == DEFAULT_ACCOUNT_RECON_TOLERANCE.quantity_abs


def test_report_is_invariant_under_input_ordering() -> None:
    p_call = _broker_position(CALL_CONID, CALL_KEY, 10.0)
    p_put = _broker_position(PUT_CONID, PUT_KEY, -4.0)
    b_call = _book_position(CALL_KEY, "10", CALL_CONID)
    b_put = _book_position(PUT_KEY, "-4", PUT_CONID)
    forward = reconcile_account(
        _snapshot(positions=(p_call, p_put)), _book_set(b_call, b_put)
    )
    reversed_report = reconcile_account(
        _snapshot(positions=(p_put, p_call)), _book_set(b_put, b_call)
    )
    forward_keys = [line.join_key for line in forward.position_lines]
    reversed_keys = [line.join_key for line in reversed_report.position_lines]
    assert forward_keys == reversed_keys
    assert all(line.status == RECON_STATUS_MATCH for line in forward.position_lines)
