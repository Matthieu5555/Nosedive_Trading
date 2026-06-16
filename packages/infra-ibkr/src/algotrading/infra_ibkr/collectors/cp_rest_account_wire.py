from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .cp_rest_wire import LooseInt, WireText

_ACCOUNT_WIRE_CONFIG = ConfigDict(extra="ignore", frozen=True)


class PositionRow(BaseModel):

    model_config = _ACCOUNT_WIRE_CONFIG

    conid: LooseInt = None
    position: float
    avg_cost: float = Field(alias="avgCost")
    market_price: float = Field(alias="mktPrice")
    market_value: float = Field(alias="mktValue")
    currency: WireText = ""
    contract_desc: WireText = Field(default="", alias="contractDesc")


def parse_position_rows(body: object) -> tuple[PositionRow, ...]:
    if not isinstance(body, Sequence) or isinstance(body, (str, bytes)):
        return ()
    return tuple(PositionRow.model_validate(row) for row in body if isinstance(row, Mapping))


class LedgerRow(BaseModel):

    model_config = _ACCOUNT_WIRE_CONFIG

    cash_balance: float = Field(alias="cashbalance")
    settled_cash: float = Field(alias="settledcash")
    net_liquidation: float = Field(alias="netliquidationvalue")
    currency: WireText = ""


def parse_ledger_rows(body: object) -> tuple[tuple[str, LedgerRow], ...]:
    if not isinstance(body, Mapping):
        return ()
    pairs: list[tuple[str, LedgerRow]] = []
    for code, value in body.items():
        if not isinstance(value, Mapping):
            continue
        pairs.append((str(code), LedgerRow.model_validate(value)))
    return tuple(pairs)


class TradeRow(BaseModel):

    model_config = _ACCOUNT_WIRE_CONFIG

    execution_id: WireText = Field(alias="execution_id")
    conid: LooseInt = None
    symbol: WireText = ""
    side: WireText = ""
    size: float
    price: float
    currency: WireText = ""
    trade_time_ms: LooseInt = Field(default=None, alias="trade_time_r")
    trade_time_text: WireText = Field(default="", alias="trade_time")


def parse_trade_rows(body: object) -> tuple[TradeRow, ...]:
    if not isinstance(body, Sequence) or isinstance(body, (str, bytes)):
        return ()
    return tuple(TradeRow.model_validate(row) for row in body if isinstance(row, Mapping))
