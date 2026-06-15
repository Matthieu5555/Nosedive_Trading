"""Typed CP REST **account** wire models (pydantic v2) — the read-only positions/cash/fills shapes.

The market-data wire shapes live in :mod:`.cp_rest_wire`; this is its account-read sibling, the
same pattern (one validated pydantic model per Client Portal payload, ``extra="ignore"``, the
broker-scalar coercers reused as ``BeforeValidator`` types) applied to the three read-only
account endpoints reconciliation reads from:

* ``GET /portfolio/{accountId}/positions/{pageId}`` → a list of :class:`PositionRow`;
* ``GET /portfolio/{accountId}/ledger`` → a currency-keyed map of :class:`LedgerRow`;
* ``GET /iserver/account/trades`` → a list of :class:`TradeRow`.

Field names track the Client Portal Web API (interactivebrokers.github.io/cpwebapi): the broker
spells them ``avgCost`` / ``mktPrice`` / ``cashbalance`` / ``trade_time_r`` etc., aliased here onto
house names so the normalizer reads typed attributes instead of spelunking an untyped ``Any``.

Row-skip semantics belong to the caller, mirroring :mod:`.cp_rest_wire`: a malformed *row* is
rejected at the normalize door (a ``ValidationError`` the collector turns into a labeled skip),
never silently coerced into a dishonest record. The numeric scalars ride pydantic's own strict-ish
float coercion (a string ``"9.20"`` the broker sends for a price is accepted; a non-numeric string
is a ``ValidationError`` — a rejected row, not a zero).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

# Reuse the market-data wire's conid coercer verbatim: the account payloads carry the same untyped
# integer conid (sometimes a string), and "coerce or skip the row" is the same job (M40 — one
# definition of the broker-scalar coercion, not a second drifting copy).
from .cp_rest_wire import LooseInt, WireText

_ACCOUNT_WIRE_CONFIG = ConfigDict(extra="ignore", frozen=True)


class PositionRow(BaseModel):
    """One ``/portfolio/{accountId}/positions/{pageId}`` row.

    ``position`` is the **signed** quantity (long positive, short negative — IBKR's convention).
    ``avgCost`` is the average cost per unit, ``mktPrice`` the last mark, ``mktValue`` the marked
    value; all in ``currency``. ``conid`` is the broker contract id (the join key). A row missing a
    required numeric field, or carrying a non-numeric one, is a ``ValidationError`` the collector
    rejects at the door — never coerced to zero.
    """

    model_config = _ACCOUNT_WIRE_CONFIG

    conid: LooseInt = None
    position: float
    avg_cost: float = Field(alias="avgCost")
    market_price: float = Field(alias="mktPrice")
    market_value: float = Field(alias="mktValue")
    currency: WireText = ""
    contract_desc: WireText = Field(default="", alias="contractDesc")


def parse_position_rows(body: object) -> tuple[PositionRow, ...]:
    """A positions response body → its rows; a non-list degrades to empty, non-objects skipped.

    A row that fails validation is **not** swallowed here — it propagates as the pydantic
    ``ValidationError`` so the collector decides (and records) the rejection. Only the
    body-is-not-a-list and entry-is-not-an-object guards live here, matching
    :func:`~.cp_rest_wire.parse_snapshot_rows`.
    """
    if not isinstance(body, Sequence) or isinstance(body, (str, bytes)):
        return ()
    return tuple(PositionRow.model_validate(row) for row in body if isinstance(row, Mapping))


class LedgerRow(BaseModel):
    """One currency entry of a ``/portfolio/{accountId}/ledger`` response.

    The ledger is an object keyed by currency code (each value is one of these), plus a synthetic
    ``BASE`` summary entry. ``cashbalance`` is the raw cash, ``settledcash`` the settled portion,
    ``netliquidationvalue`` the currency's NLV — all signed (a debit is negative). ``currency`` is
    carried in the value too, but the map key is authoritative (the collector passes it in).
    """

    model_config = _ACCOUNT_WIRE_CONFIG

    cash_balance: float = Field(alias="cashbalance")
    settled_cash: float = Field(alias="settledcash")
    net_liquidation: float = Field(alias="netliquidationvalue")
    currency: WireText = ""


def parse_ledger_rows(body: object) -> tuple[tuple[str, LedgerRow], ...]:
    """A ledger response body → ``(currency_code, row)`` pairs; a non-mapping degrades to empty.

    The ledger is keyed by currency, so the **map key** is the authoritative currency (returned
    beside the validated value). A value that is not an object is skipped; a value that fails
    validation propagates as a ``ValidationError`` for the collector to record.
    """
    if not isinstance(body, Mapping):
        return ()
    pairs: list[tuple[str, LedgerRow]] = []
    for code, value in body.items():
        if not isinstance(value, Mapping):
            continue
        pairs.append((str(code), LedgerRow.model_validate(value)))
    return tuple(pairs)


class TradeRow(BaseModel):
    """One ``/iserver/account/trades`` execution (fill) row.

    ``execution_id`` is the broker's globally-unique execution id (the natural key). ``side`` is
    IBKR's raw ``"B"`` / ``"S"`` — normalized to ``BUY`` / ``SELL`` downstream. ``size`` is the
    **unsigned** filled magnitude and ``price`` the fill price, in ``currency``. ``trade_time_r``
    is the execution's **own venue time** as epoch-ms (the no-look-ahead timestamp — a fill is
    stamped when it happened at the venue, never at our read clock); ``trade_time`` is its textual
    form (carried for diagnostics, not the stamp). ``conid`` is the broker contract id.
    """

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
    """A trades response body → its rows; a non-list degrades to empty, non-objects skipped.

    As with positions, a validation failure on a *row* propagates so the collector records the
    rejection rather than silently dropping a real fill.
    """
    if not isinstance(body, Sequence) or isinstance(body, (str, bytes)):
        return ()
    return tuple(TradeRow.model_validate(row) for row in body if isinstance(row, Mapping))
