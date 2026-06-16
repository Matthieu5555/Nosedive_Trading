from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from algotrading.infra.contracts import (
    BrokerCashBalance,
    BrokerFill,
    BrokerPosition,
    validate_record,
)
from algotrading.infra_ibkr.collectors.cp_rest_account import (
    CpRestAccountCollector,
    collect_broker_account,
)

from .conftest import FakeCpTransport

_ACCOUNT = "DU1234567"
_READ_TS = datetime(2026, 5, 29, 16, 0, 0, tzinfo=UTC)
_CONID = 265598

_VENUE_TS = datetime(2026, 5, 29, 15, 29, 59, tzinfo=UTC)
_VENUE_MS = 1_780_068_599_000

_POSITIONS_BODY = [
    {
        "conid": _CONID,
        "position": -3.0,
        "avgCost": 9.20,
        "mktPrice": 9.31,
        "mktValue": -2793.0,
        "currency": "USD",
        "contractDesc": "SPY 20260626 758 C",
    }
]
_LEDGER_BODY = {
    "USD": {
        "cashbalance": 100000.0,
        "settledcash": 98000.0,
        "netliquidationvalue": 109310.0,
        "currency": "USD",
    },
    "BASE": {
        "cashbalance": 100000.0,
        "settledcash": 98000.0,
        "netliquidationvalue": 109310.0,
        "currency": "BASE",
    },
}
_TRADES_BODY = [
    {
        "execution_id": "0000e0d5.0000abcd.01",
        "conid": _CONID,
        "symbol": "SPY",
        "side": "S",
        "size": 3.0,
        "price": 9.31,
        "currency": "USD",
        "trade_time_r": _VENUE_MS,
        "trade_time": "20260529-15:29:59",
    }
]


def _transport(**overrides: Any) -> FakeCpTransport:
    routes: dict[str, Any] = {
        f"/portfolio/{_ACCOUNT}/positions/0": _POSITIONS_BODY,
        f"/portfolio/{_ACCOUNT}/ledger": _LEDGER_BODY,
        "/iserver/account/trades": _TRADES_BODY,
    }
    routes.update(overrides)
    return FakeCpTransport(get_routes=routes)


def _collector(transport: FakeCpTransport) -> CpRestAccountCollector:
    return CpRestAccountCollector(transport, account_id=_ACCOUNT, now_fn=lambda: _READ_TS)


def test_positions_normalize_with_signed_quantity_and_mapped_fields() -> None:
    positions = _collector(_transport()).read_positions()
    assert len(positions) == 1
    pos = positions[0]
    assert pos == BrokerPosition(
        as_of_ts=_READ_TS,
        account_id=_ACCOUNT,
        conid=_CONID,
        contract_key=f"conid={_CONID}",
        quantity=-3.0,
        avg_cost=9.20,
        market_price=9.31,
        market_value=-2793.0,
        currency="USD",
    )
    validate_record("broker_positions", pos)


def test_cash_balances_one_row_per_currency_keyed_by_map_key() -> None:
    balances = _collector(_transport()).read_cash_balances()
    by_currency = {b.currency: b for b in balances}
    assert set(by_currency) == {"USD", "BASE"}
    usd = by_currency["USD"]
    assert usd == BrokerCashBalance(
        as_of_ts=_READ_TS,
        account_id=_ACCOUNT,
        currency="USD",
        cash_balance=100000.0,
        settled_cash=98000.0,
        net_liquidation=109310.0,
    )
    validate_record("broker_cash_balances", usd)


def test_fill_is_stamped_at_its_own_venue_time_not_the_read_clock() -> None:
    fills = _collector(_transport()).read_fills()
    assert len(fills) == 1
    fill = fills[0]
    assert fill == BrokerFill(
        account_id=_ACCOUNT,
        execution_id="0000e0d5.0000abcd.01",
        conid=_CONID,
        contract_key=f"conid={_CONID}",
        side="SELL",
        quantity=3.0,
        price=9.31,
        currency="USD",
        venue_ts=_VENUE_TS,
        trade_date=date(2026, 5, 29),
    )
    assert fill.venue_ts == _VENUE_TS
    assert fill.venue_ts < _READ_TS
    validate_record("broker_fills", fill)


def test_collect_assembles_one_coherent_snapshot_at_one_instant() -> None:
    snapshot = collect_broker_account(
        _transport(), account_id=_ACCOUNT, now_fn=lambda: _READ_TS
    )
    assert snapshot.account_id == _ACCOUNT
    assert snapshot.as_of_ts == _READ_TS
    assert len(snapshot.positions) == 1
    assert len(snapshot.cash_balances) == 2
    assert len(snapshot.fills) == 1
    assert all(p.as_of_ts == _READ_TS for p in snapshot.positions)
    assert all(c.as_of_ts == _READ_TS for c in snapshot.cash_balances)
    assert snapshot.fills[0].venue_ts == _VENUE_TS


def test_collector_is_read_only_only_portfolio_and_trades_gets_no_post() -> None:
    transport = _transport()
    collect_broker_account(transport, account_id=_ACCOUNT, now_fn=lambda: _READ_TS)
    assert sorted(set(transport.get_paths)) == [
        "/iserver/account/trades",
        f"/portfolio/{_ACCOUNT}/ledger",
        f"/portfolio/{_ACCOUNT}/positions/0",
    ]
    assert transport.post_paths == []
    assert not any("order" in path for path in transport.get_paths + transport.post_paths)


def test_a_malformed_position_row_is_rejected_not_coerced() -> None:
    bad = [
        _POSITIONS_BODY[0],
        {"conid": 111, "position": 1.0, "avgCost": 1.0, "mktValue": 1.0, "currency": "USD"},
    ]
    positions = _collector(
        _transport(**{f"/portfolio/{_ACCOUNT}/positions/0": bad})
    ).read_positions()
    assert [p.conid for p in positions] == [_CONID]


def test_a_fill_with_an_unrecognized_side_is_dropped() -> None:
    bad = [{**_TRADES_BODY[0], "side": "?", "execution_id": "x.y.z"}]
    fills = _collector(_transport(**{"/iserver/account/trades": bad})).read_fills()
    assert fills == ()


def test_a_position_row_without_a_conid_is_skipped() -> None:
    bad = [{**_POSITIONS_BODY[0], "conid": None}]
    fills = _collector(
        _transport(**{f"/portfolio/{_ACCOUNT}/positions/0": bad})
    ).read_positions()
    assert fills == ()


def test_empty_account_reads_yield_empty_tuples_not_errors() -> None:
    empty = _transport(
        **{
            f"/portfolio/{_ACCOUNT}/positions/0": [],
            f"/portfolio/{_ACCOUNT}/ledger": {},
            "/iserver/account/trades": [],
        }
    )
    snapshot = collect_broker_account(empty, account_id=_ACCOUNT, now_fn=lambda: _READ_TS)
    assert snapshot.positions == ()
    assert snapshot.cash_balances == ()
    assert snapshot.fills == ()
