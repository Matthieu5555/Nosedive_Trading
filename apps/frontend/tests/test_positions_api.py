from __future__ import annotations

import json
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

AS_OF = datetime(2026, 6, 12, 15, 30, tzinfo=UTC)
VENUE_TS = datetime(2026, 6, 12, 14, 0, 1, tzinfo=UTC)
TRADE_DATE = date(2026, 6, 12)
UNDERLYING = "SX5E"
OTHER_UNDERLYING = "DAX"

CALL_4400 = "SX5E|OPT|EUREX|EUR|10|o-C-4400|2026-09-18|4400|C"
PUT_4200 = "SX5E|OPT|EUREX|EUR|10|o-P-4200|2026-09-18|4200|P"
CLOSED_KEY = "SX5E|OPT|EUREX|EUR|10|o-C-4600|2026-09-18|4600|C"
DAX_CALL = "DAX|OPT|EUREX|EUR|5|o-DAX-C|2026-09-18|18000|C"

CALL_MULT = 10.0
PUT_MULT = 10.0

CALL_DELTA = 0.55
CALL_GAMMA = 0.02
CALL_VEGA = 0.31
CALL_THETA = -0.05
CALL_RHO = 0.04
CALL_PRICE = 12.5
CALL_DOLLAR_DELTA = 58.5
CALL_DOLLAR_GAMMA = 7.6
CALL_DOLLAR_VEGA = 0.31
CALL_DOLLAR_THETA = -0.000041
CALL_DOLLAR_RHO = 0.0005

PUT_DELTA = -0.30
PUT_GAMMA = 0.018
PUT_VEGA = 0.28
PUT_THETA = -0.04
PUT_RHO = -0.03
PUT_PRICE = 8.0
PUT_DOLLAR_DELTA = -31.0
PUT_DOLLAR_GAMMA = 6.0
PUT_DOLLAR_VEGA = 0.28
PUT_DOLLAR_THETA = -0.000035
PUT_DOLLAR_RHO = -0.0004


def _prov(source: str) -> ProvenanceStamp:
    return stamp(
        calc_ts=AS_OF,
        code_version="positions-readback-test",
        config_hashes={"cfg": "positions-readback"},
        source_records=(source_ref("order_tickets", "bsk-1", source),),
        source_timestamps=(AS_OF,),
    )


def _fill(
    *,
    fill_id: str,
    contract_key: str,
    signed_qty: str,
    price: float,
    underlying: str = UNDERLYING,
    broker_contract_id: str | None = None,
) -> Fill:
    return Fill(
        fill_id=fill_id,
        booking_id="bkg-1",
        source_basket_id="bsk-1",
        trade_date=TRADE_DATE,
        underlying=underlying,
        contract_key=contract_key,
        signed_qty=Decimal(signed_qty),
        price=price,
        fill_ts=VENUE_TS,
        provenance=_prov(f"fill:{fill_id}"),
        broker_contract_id=broker_contract_id,
    )


SEED_FILLS = (
    _fill(
        fill_id="f-1",
        contract_key=CALL_4400,
        signed_qty="3",
        price=12.4,
        broker_contract_id="o-C-4400",
    ),
    _fill(
        fill_id="f-2",
        contract_key=CALL_4400,
        signed_qty="2",
        price=12.6,
        broker_contract_id="o-C-4400",
    ),
    _fill(fill_id="f-3", contract_key=PUT_4200, signed_qty="-4", price=8.1),
    _fill(fill_id="f-4", contract_key=CLOSED_KEY, signed_qty="2", price=5.0),
    _fill(fill_id="f-5", contract_key=CLOSED_KEY, signed_qty="-2", price=5.5),
    _fill(
        fill_id="f-6",
        contract_key=DAX_CALL,
        signed_qty="1",
        price=20.0,
        underlying=OTHER_UNDERLYING,
    ),
)

EXPECTED_CALL_QTY = 5.0
EXPECTED_PUT_QTY = -4.0


def _pricing_row(
    *,
    contract_key: str,
    price: float,
    delta: float,
    gamma: float,
    vega: float,
    theta: float,
    rho: float,
    dollar_delta: float,
    dollar_gamma: float,
    dollar_vega: float,
    dollar_theta: float,
    dollar_rho: float,
    snapshot_ts: datetime = AS_OF,
) -> tables.PricingResult:
    return tables.PricingResult(
        snapshot_ts=snapshot_ts,
        contract_key=contract_key,
        pricer_version="px-positions-readback",
        price=price,
        delta=delta,
        gamma=gamma,
        vega=vega,
        theta=theta,
        rho=rho,
        dollar_delta=dollar_delta,
        dollar_gamma=dollar_gamma,
        dollar_vega=dollar_vega,
        dollar_theta=dollar_theta,
        dollar_rho=dollar_rho,
        source_snapshot_ts=snapshot_ts,
        provenance=_prov(f"px:{contract_key}"),
    )


def _seed_pricing(store: ParquetStore) -> None:
    store.write(
        "pricing_results",
        [
            _pricing_row(
                contract_key=CALL_4400,
                price=CALL_PRICE,
                delta=CALL_DELTA,
                gamma=CALL_GAMMA,
                vega=CALL_VEGA,
                theta=CALL_THETA,
                rho=CALL_RHO,
                dollar_delta=CALL_DOLLAR_DELTA,
                dollar_gamma=CALL_DOLLAR_GAMMA,
                dollar_vega=CALL_DOLLAR_VEGA,
                dollar_theta=CALL_DOLLAR_THETA,
                dollar_rho=CALL_DOLLAR_RHO,
            ),
            _pricing_row(
                contract_key=PUT_4200,
                price=PUT_PRICE,
                delta=PUT_DELTA,
                gamma=PUT_GAMMA,
                vega=PUT_VEGA,
                theta=PUT_THETA,
                rho=PUT_RHO,
                dollar_delta=PUT_DOLLAR_DELTA,
                dollar_gamma=PUT_DOLLAR_GAMMA,
                dollar_vega=PUT_DOLLAR_VEGA,
                dollar_theta=PUT_DOLLAR_THETA,
                dollar_rho=PUT_DOLLAR_RHO,
            ),
        ],
    )


def _seed_ledger(store_root: Path) -> None:
    ledger_path = store_root / "booking" / "fills.jsonl"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger = JsonlFillsLedger(ledger_path)
    ledger.append_many(SEED_FILLS)


def _seeded_context(store_root: Path) -> AppContext:
    store = ParquetStore(store_root)
    _seed_pricing(store)
    _seed_ledger(store_root)
    return AppContext(
        store_root=store_root,
        configs_dir=store_root.parent / "configs",
        store=store,
        default_underlying=UNDERLYING,
    )


@pytest.fixture
def positions_client(tmp_path: Path) -> Iterator[TestClient]:
    ctx = _seeded_context(tmp_path / "data")
    with TestClient(create_app(ctx)) as client:
        yield client


def test_fills_endpoint_returns_every_appended_fill(positions_client: TestClient) -> None:
    body = positions_client.get("/api/positions/fills").json()
    assert body["n_fills"] == len(SEED_FILLS)
    by_id = {row["fill_id"]: row for row in body["fills"]}
    assert set(by_id) == {"f-1", "f-2", "f-3", "f-4", "f-5", "f-6"}


def test_fills_endpoint_preserves_sign_units_identifiers_and_venue_time(
    positions_client: TestClient,
) -> None:
    body = positions_client.get("/api/positions/fills").json()
    by_id = {row["fill_id"]: row for row in body["fills"]}
    short_leg = by_id["f-3"]
    assert short_leg["contract_key"] == PUT_4200
    assert short_leg["signed_qty"] == "-4"
    assert short_leg["underlying"] == UNDERLYING
    assert short_leg["price"] == 8.1
    assert short_leg["mode"] == "paper"
    assert short_leg["fill_ts"] == VENUE_TS.isoformat()
    assert by_id["f-1"]["broker_contract_id"] == "o-C-4400"


def test_fills_endpoint_filters_by_underlying(positions_client: TestClient) -> None:
    body = positions_client.get(
        "/api/positions/fills", params={"underlying": OTHER_UNDERLYING}
    ).json()
    assert body["n_fills"] == 1
    assert body["fills"][0]["fill_id"] == "f-6"


def test_positions_accumulate_partial_fills_and_drop_closed_legs(
    positions_client: TestClient,
) -> None:
    body = positions_client.get("/api/positions").json()
    by_key = {line["contract_key"]: line for line in body["lines"]}
    assert CLOSED_KEY not in by_key
    assert by_key[CALL_4400]["quantity"] == pytest.approx(EXPECTED_CALL_QTY)
    assert by_key[PUT_4200]["quantity"] == pytest.approx(EXPECTED_PUT_QTY)


def test_position_line_greeks_are_signed_qty_times_banked_greeks(
    positions_client: TestClient,
) -> None:
    body = positions_client.get("/api/positions").json()
    call = {line["contract_key"]: line for line in body["lines"]}[CALL_4400]
    scale = EXPECTED_CALL_QTY * CALL_MULT
    assert call["greeks"]["delta"]["raw"] == pytest.approx(CALL_DELTA)
    assert call["greeks"]["delta"]["position"] == pytest.approx(CALL_DELTA * scale)
    assert call["greeks"]["delta"]["dollar"] == pytest.approx(
        CALL_DOLLAR_DELTA * EXPECTED_CALL_QTY
    )
    assert call["greeks"]["gamma"]["dollar"] == pytest.approx(
        CALL_DOLLAR_GAMMA * EXPECTED_CALL_QTY
    )
    assert call["greeks"]["delta"]["unit"] == "$ per $1 of underlying"
    assert call["mark_price"] == pytest.approx(CALL_PRICE)
    assert call["market_value"] == pytest.approx(CALL_PRICE * EXPECTED_CALL_QTY * CALL_MULT)


def test_book_greeks_are_additive_over_priced_legs(positions_client: TestClient) -> None:
    body = positions_client.get("/api/positions").json()
    expected_dollar_delta = (
        CALL_DOLLAR_DELTA * EXPECTED_CALL_QTY + PUT_DOLLAR_DELTA * EXPECTED_PUT_QTY
    )
    expected_dollar_vega = (
        CALL_DOLLAR_VEGA * EXPECTED_CALL_QTY + PUT_DOLLAR_VEGA * EXPECTED_PUT_QTY
    )
    expected_market_value = (
        CALL_PRICE * EXPECTED_CALL_QTY * CALL_MULT + PUT_PRICE * EXPECTED_PUT_QTY * PUT_MULT
    )
    assert body["book"]["delta"]["dollar"] == pytest.approx(expected_dollar_delta)
    assert body["book"]["vega"]["dollar"] == pytest.approx(expected_dollar_vega)
    assert body["book"]["market_value"] == pytest.approx(expected_market_value)


def test_positions_filtered_by_underlying_exclude_other_books(
    positions_client: TestClient,
) -> None:
    body = positions_client.get(
        "/api/positions", params={"underlying": OTHER_UNDERLYING}
    ).json()
    keys = {line["contract_key"] for line in body["lines"]}
    assert keys == {DAX_CALL}


def test_position_with_no_banked_pricing_is_flagged_not_silently_zeroed(
    positions_client: TestClient,
) -> None:
    body = positions_client.get(
        "/api/positions", params={"underlying": OTHER_UNDERLYING}
    ).json()
    assert DAX_CALL in body["unpriced_contract_keys"]
    assert body["priced_contract_keys"] == 0
    dax = body["lines"][0]
    assert dax["greeks"]["delta"]["dollar"] == 0.0
    assert dax["market_value"] == 0.0


def test_empty_ledger_yields_empty_book(tmp_path: Path) -> None:
    store_root = tmp_path / "data"
    ctx = AppContext(
        store_root=store_root,
        configs_dir=tmp_path / "configs",
        store=ParquetStore(store_root),
        default_underlying=UNDERLYING,
    )
    with TestClient(create_app(ctx)) as client:
        fills = client.get("/api/positions/fills").json()
        positions = client.get("/api/positions").json()
    assert fills == {"trade_date": None, "underlying": None, "n_fills": 0, "fills": []}
    assert positions["n_lines"] == 0
    assert positions["book"]["delta"]["dollar"] == 0.0


def test_fills_to_position_seam_field_names_and_sign_convention() -> None:
    from algotrading.frontend.positions_read import fills_view, position_book
    from algotrading.infra.risk import PositionSet

    book = position_book(
        PositionSet(positions=(), source="booked", source_ts=AS_OF),
        [],
    )
    assert book.source == "booked"
    fill_row = fills_view([SEED_FILLS[2]])[0]
    assert set(fill_row) == {
        "fill_id",
        "booking_id",
        "source_basket_id",
        "trade_date",
        "underlying",
        "contract_key",
        "signed_qty",
        "price",
        "fill_ts",
        "mode",
        "broker_contract_id",
    }
    assert fill_row["signed_qty"] == "-4"
    assert Fill.__dataclass_fields__["signed_qty"].type == "Decimal"


def test_latest_pricing_snapshot_wins_no_lookahead() -> None:
    from algotrading.frontend.positions_read import position_book
    from algotrading.infra.risk import Position, PositionSet

    stale = _pricing_row(
        contract_key=CALL_4400,
        price=1.0,
        delta=0.10,
        gamma=0.0,
        vega=0.0,
        theta=0.0,
        rho=0.0,
        dollar_delta=1.0,
        dollar_gamma=0.0,
        dollar_vega=0.0,
        dollar_theta=0.0,
        dollar_rho=0.0,
        snapshot_ts=datetime(2026, 6, 11, 15, 30, tzinfo=UTC),
    )
    fresh = _pricing_row(
        contract_key=CALL_4400,
        price=CALL_PRICE,
        delta=CALL_DELTA,
        gamma=CALL_GAMMA,
        vega=CALL_VEGA,
        theta=CALL_THETA,
        rho=CALL_RHO,
        dollar_delta=CALL_DOLLAR_DELTA,
        dollar_gamma=CALL_DOLLAR_GAMMA,
        dollar_vega=CALL_DOLLAR_VEGA,
        dollar_theta=CALL_DOLLAR_THETA,
        dollar_rho=CALL_DOLLAR_RHO,
    )
    pos = PositionSet(
        positions=(Position(contract_key=CALL_4400, quantity=Decimal("5")),),
        source="booked",
        source_ts=AS_OF,
    )
    book = position_book(pos, [stale, fresh])
    assert book.lines[0].greeks["delta"].raw == pytest.approx(CALL_DELTA)


GOLDEN_PATH = Path(__file__).parent / "golden" / "positions_book.json"


def test_positions_book_matches_golden(positions_client: TestClient) -> None:
    body = positions_client.get("/api/positions").json()
    body.pop("source_ts")
    expected = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    assert body == expected, (
        "regenerate with: "
        "uv run python apps/frontend/tests/golden/regenerate_positions_book.py"
    )
