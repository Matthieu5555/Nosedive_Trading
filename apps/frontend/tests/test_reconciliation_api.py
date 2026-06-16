from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from algotrading.core.provenance import ProvenanceStamp, source_ref, stamp
from algotrading.execution import Fill, JsonlFillsLedger
from algotrading.frontend.app import create_app
from algotrading.frontend.context import AppContext
from algotrading.infra.contracts import tables
from algotrading.infra.storage import ParquetStore
from fastapi.testclient import TestClient

AS_OF = datetime(2026, 6, 12, 16, 30, tzinfo=UTC)
EARLIER = datetime(2026, 6, 11, 16, 30, tzinfo=UTC)
VENUE_TS = datetime(2026, 6, 12, 14, 0, 1, tzinfo=UTC)
TRADE_DATE = date(2026, 6, 12)
ACCOUNT = "DUQ574355"

CALL_KEY = "SX5E|OPT|EUREX|EUR|10|o-C-4400|2026-09-18|4400|C"
PUT_KEY = "SX5E|OPT|EUREX|EUR|10|o-P-4200|2026-09-18|4200|P"
CALL_CONID = 265598
PUT_CONID = 311042


def _prov(source: str) -> ProvenanceStamp:
    return stamp(
        calc_ts=AS_OF,
        code_version="recon-readback-test",
        config_hashes={"cfg": "recon-readback"},
        source_records=(source_ref("order_tickets", "bsk-1", source),),
        source_timestamps=(AS_OF,),
    )


def _fill(*, fill_id: str, contract_key: str, signed_qty: str, conid: int) -> Fill:
    return Fill(
        fill_id=fill_id,
        booking_id="bkg-1",
        source_basket_id="bsk-1",
        trade_date=TRADE_DATE,
        underlying="SX5E",
        contract_key=contract_key,
        signed_qty=Decimal(signed_qty),
        price=12.4,
        fill_ts=VENUE_TS,
        provenance=_prov(f"fill:{fill_id}"),
        broker_contract_id=str(conid),
    )


def _broker_position(
    *, conid: int, contract_key: str, quantity: float, as_of: datetime = AS_OF
) -> tables.BrokerPosition:
    return tables.BrokerPosition(
        as_of_ts=as_of,
        account_id=ACCOUNT,
        conid=conid,
        contract_key=contract_key,
        quantity=quantity,
        avg_cost=12.0,
        market_price=12.5,
        market_value=quantity * 125.0,
        currency="EUR",
    )


def _broker_fill(*, conid: int, contract_key: str, side: str, quantity: float) -> tables.BrokerFill:
    return tables.BrokerFill(
        account_id=ACCOUNT,
        execution_id=f"exec-{conid}-{side}",
        conid=conid,
        contract_key=contract_key,
        side=side,
        quantity=quantity,
        price=12.4,
        currency="EUR",
        venue_ts=VENUE_TS,
        trade_date=TRADE_DATE,
    )


def _seed_store(store: ParquetStore) -> None:
    store.write(
        "broker_positions",
        [
            _broker_position(conid=CALL_CONID, contract_key=CALL_KEY, quantity=5.0),
            _broker_position(conid=PUT_CONID, contract_key=PUT_KEY, quantity=-4.0),
            _broker_position(
                conid=CALL_CONID, contract_key=CALL_KEY, quantity=999.0, as_of=EARLIER
            ),
        ],
    )
    store.write(
        "broker_cash_balances",
        [
            tables.BrokerCashBalance(
                as_of_ts=AS_OF,
                account_id=ACCOUNT,
                currency="EUR",
                cash_balance=100000.0,
                settled_cash=98000.0,
                net_liquidation=109310.0,
            )
        ],
    )
    store.write(
        "broker_fills",
        [
            _broker_fill(conid=CALL_CONID, contract_key=CALL_KEY, side="BUY", quantity=5.0),
            _broker_fill(conid=PUT_CONID, contract_key=PUT_KEY, side="SELL", quantity=4.0),
        ],
    )


def _seed_ledger(store_root: Path) -> None:
    ledger_path = store_root / "booking" / "fills.jsonl"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger = JsonlFillsLedger(ledger_path)
    ledger.append_many(
        (
            _fill(fill_id="f-1", contract_key=CALL_KEY, signed_qty="3", conid=CALL_CONID),
            _fill(fill_id="f-2", contract_key=CALL_KEY, signed_qty="2", conid=CALL_CONID),
            _fill(fill_id="f-3", contract_key=PUT_KEY, signed_qty="-4", conid=PUT_CONID),
        )
    )


@pytest.fixture
def recon_client(tmp_path: Path) -> Iterator[TestClient]:
    store_root = tmp_path / "data"
    store = ParquetStore(store_root)
    _seed_store(store)
    _seed_ledger(store_root)
    ctx = AppContext(
        store_root=store_root,
        configs_dir=store_root.parent / "configs",
        store=store,
        default_underlying="SX5E",
    )
    with TestClient(create_app(ctx)) as client:
        yield client


def test_reconciliation_endpoint_reports_all_matches_for_agreeing_book(
    recon_client: TestClient,
) -> None:
    body = recon_client.get("/api/reconciliation").json()
    assert body["account_id"] == ACCOUNT
    assert body["ok"] is True
    assert body["positions"]["counts"]["match"] == 2
    assert body["positions"]["counts"]["break"] == 0
    by_key = {line["join_key"]: line for line in body["positions"]["lines"]}
    assert by_key[str(CALL_CONID)]["broker_quantity"] == pytest.approx(5.0)
    assert by_key[str(CALL_CONID)]["book_quantity"] == pytest.approx(5.0)
    assert by_key[str(PUT_CONID)]["book_quantity"] == pytest.approx(-4.0)


def test_reconciliation_endpoint_uses_latest_broker_snapshot(recon_client: TestClient) -> None:
    body = recon_client.get("/api/reconciliation").json()
    call_line = {line["join_key"]: line for line in body["positions"]["lines"]}[str(CALL_CONID)]
    assert call_line["broker_quantity"] == pytest.approx(5.0)
    assert call_line["status"] == "match"


def test_reconciliation_endpoint_surfaces_cash_lines(recon_client: TestClient) -> None:
    body = recon_client.get("/api/reconciliation").json()
    assert body["cash"]["n_lines"] == 1
    cash = body["cash"]["lines"][0]
    assert cash["currency"] == "EUR"
    assert cash["broker_net_liquidation"] == pytest.approx(109310.0)
    assert cash["status"] == "broker_only"


def test_reconciliation_endpoint_matches_netted_fills(recon_client: TestClient) -> None:
    body = recon_client.get("/api/reconciliation").json()
    assert body["fills"]["counts"]["match"] == 2
    by_key = {line["join_key"]: line for line in body["fills"]["lines"]}
    assert by_key[str(CALL_CONID)]["broker_signed_quantity"] == pytest.approx(5.0)
    assert by_key[str(CALL_CONID)]["book_signed_quantity"] == pytest.approx(5.0)
    assert by_key[str(PUT_CONID)]["broker_signed_quantity"] == pytest.approx(-4.0)


def test_reconciliation_endpoint_400_when_no_broker_account(tmp_path: Path) -> None:
    store_root = tmp_path / "empty"
    store = ParquetStore(store_root)
    ctx = AppContext(
        store_root=store_root,
        configs_dir=store_root.parent / "configs",
        store=store,
        default_underlying="SX5E",
    )
    with TestClient(create_app(ctx)) as client:
        resp = client.get("/api/reconciliation")
    assert resp.status_code == 400
    assert resp.json()["error"] == "no_broker_account"
