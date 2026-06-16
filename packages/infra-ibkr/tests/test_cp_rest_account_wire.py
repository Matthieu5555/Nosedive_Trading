from __future__ import annotations

import pytest
from algotrading.infra_ibkr.collectors.cp_rest_account_wire import (
    LedgerRow,
    PositionRow,
    TradeRow,
    parse_ledger_rows,
    parse_position_rows,
    parse_trade_rows,
)
from pydantic import ValidationError


def test_position_row_maps_broker_aliases_onto_house_names() -> None:
    row = PositionRow.model_validate(
        {"conid": 42, "position": -3.0, "avgCost": 9.2, "mktPrice": 9.31, "mktValue": -27.9,
         "currency": "USD", "contractDesc": "X"}
    )
    assert row.conid == 42
    assert row.position == -3.0
    assert row.avg_cost == 9.2
    assert row.market_price == 9.31
    assert row.market_value == -27.9
    assert row.currency == "USD"


def test_position_row_accepts_a_string_numeric_but_rejects_a_non_numeric() -> None:
    ok = PositionRow.model_validate(
        {"conid": 1, "position": "2", "avgCost": "9.2", "mktPrice": "9.3", "mktValue": "18.6"}
    )
    assert ok.position == 2.0 and ok.avg_cost == 9.2
    with pytest.raises(ValidationError):
        PositionRow.model_validate(
            {"conid": 1, "position": "abc", "avgCost": 9.2, "mktPrice": 9.3, "mktValue": 18.6}
        )


def test_ledger_row_maps_lowercase_balance_aliases() -> None:
    row = LedgerRow.model_validate(
        {"cashbalance": 100.0, "settledcash": 98.0, "netliquidationvalue": 109.0, "currency": "USD"}
    )
    assert row.cash_balance == 100.0
    assert row.settled_cash == 98.0
    assert row.net_liquidation == 109.0


def test_trade_row_maps_venue_time_alias_and_keeps_side_raw() -> None:
    row = TradeRow.model_validate(
        {"execution_id": "e1", "conid": 7, "side": "B", "size": 5.0, "price": 1.5,
         "trade_time_r": 1_780_068_599_000, "trade_time": "t"}
    )
    assert row.execution_id == "e1"
    assert row.side == "B"
    assert row.trade_time_ms == 1_780_068_599_000


def test_parse_ledger_keys_by_currency_and_skips_non_object_values() -> None:
    pairs = parse_ledger_rows(
        {
            "USD": {"cashbalance": 1.0, "settledcash": 1.0, "netliquidationvalue": 1.0},
            "junk": "not-an-object",
        }
    )
    assert [code for code, _ in pairs] == ["USD"]


@pytest.mark.parametrize("parser", [parse_position_rows, parse_trade_rows])
def test_list_parsers_degrade_a_non_list_body_to_empty(parser: object) -> None:
    assert parser({"not": "a list"}) == ()  # type: ignore[operator]
    assert parser(None) == ()  # type: ignore[operator]


def test_parse_ledger_degrades_a_non_mapping_body_to_empty() -> None:
    assert parse_ledger_rows([1, 2, 3]) == ()
    assert parse_ledger_rows(None) == ()
