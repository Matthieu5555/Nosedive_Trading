from __future__ import annotations

import json
import math
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from algotrading.core import source_ref, stamp
from algotrading.frontend.app import create_app
from algotrading.frontend.context import AppContext
from algotrading.infra.contracts import (
    InstrumentKey,
    InstrumentMaster,
    ProjectedOptionAnalytics,
)
from algotrading.infra.pricing import UNIT_STRINGS
from algotrading.infra.risk.decorrelation import DECORRELATION_VERSION
from algotrading.infra.storage import ParquetStore
from fastapi.testclient import TestClient

_TS = datetime(2026, 6, 5, 20, 0, tzinfo=UTC)
_TRADE = date(2026, 6, 5)
_EXPIRY = date(2026, 9, 4)
_CONFIGS = Path("configs").resolve()
_UND_A = "AAA"
_UND_B = "BBB"


def _analytics_row(underlying: str, *, strike: float, vega: float) -> ProjectedOptionAnalytics:
    return ProjectedOptionAnalytics(
        snapshot_ts=_TS,
        provider="ibkr",
        underlying=underlying,
        tenor_label="1m",
        maturity_years=1.0 / 12.0,
        delta_band="atm",
        target_delta=0.50,
        log_moneyness=0.0,
        strike=strike,
        forward_price=strike,
        implied_vol=0.2,
        total_variance=0.2 * 0.2 / 12.0,
        price=2.30,
        delta=0.5,
        gamma=0.02,
        vega=vega,
        theta=-0.05,
        rho=0.04,
        dollar_delta=500.0,
        dollar_gamma=0.02,
        dollar_vega=vega,
        dollar_delta_unit=UNIT_STRINGS["dollar_delta"],
        dollar_gamma_unit=UNIT_STRINGS["dollar_gamma_one_pct"],
        dollar_vega_unit=UNIT_STRINGS["dollar_vega"],
        model_version="svi-test",
        pricer_version="px-test",
        source_snapshot_ts=_TS,
        provenance=stamp(
            calc_ts=_TS,
            code_version="decorrelation-seam-test",
            config_hashes={"cfg": "cfg"},
            source_records=(source_ref("raw_market_events", "sess", "evt"),),
            source_timestamps=(_TS,),
        ),
    )


def _instrument_master(underlying: str, *, strike: float) -> InstrumentMaster:
    key = InstrumentKey(
        underlying_symbol=underlying,
        security_type="OPT",
        exchange="EUREX",
        currency="EUR",
        multiplier=10.0,
        broker_contract_id=f"o-{underlying}",
        expiry=_EXPIRY,
        strike=strike,
        option_right="C",
    )
    return InstrumentMaster(
        instrument_key=key.canonical(),
        as_of_date=_TRADE,
        instrument=key,
        raw_broker_payload="{}",
    )


@pytest.fixture
def compose_client(tmp_path: Path) -> Iterator[TestClient]:
    store_root = tmp_path / "data"
    store = ParquetStore(store_root)
    store.write(
        "projected_option_analytics",
        [
            _analytics_row(_UND_A, strike=100.0, vega=0.31),
            _analytics_row(_UND_B, strike=50.0, vega=0.17),
        ],
    )
    store.write(
        "instrument_master",
        [
            _instrument_master(_UND_A, strike=100.0),
            _instrument_master(_UND_B, strike=50.0),
        ],
    )
    ctx = AppContext(store_root=store_root, configs_dir=_CONFIGS, store=store)
    with TestClient(create_app(ctx)) as client:
        yield client


def _leg(underlying: str, side: str, quantity: float) -> dict[str, object]:
    signed = quantity if side == "long" else -quantity
    return {
        "instrument_kind": "option",
        "side": side,
        "quantity": signed,
        "underlying": underlying,
        "tenor_label": "1m",
        "delta_band": "atm",
    }


def _layer(label: str, basket_id: str, underlying: str, side: str, qty: float) -> dict[str, object]:
    return {
        "label": label,
        "basket_id": basket_id,
        "underlying": underlying,
        "legs": [_leg(underlying, side, qty)],
    }


_LAYER_A = _layer("vol-seller", "L1", _UND_A, "short", 2.0)
_LAYER_B = _layer("crash-hedge", "L2", _UND_B, "long", 3.0)


def _compose(client: TestClient, layers: list[dict[str, Any]]) -> dict[str, Any]:
    response = client.post(
        "/api/compose",
        json={"book_id": "BK-decorr", "trade_date": _TRADE.isoformat(), "layers": layers},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_compose_response_carries_additive_decorrelation_block(
    compose_client: TestClient,
) -> None:
    book = _compose(compose_client, [_LAYER_A, _LAYER_B])
    assert "decorrelation" in book
    for legacy_key in ("combined", "layers", "surface", "diversification_ratio"):
        assert legacy_key in book, f"existing field {legacy_key} must remain"

    block = book["decorrelation"]
    assert block["version"] == DECORRELATION_VERSION
    assert block["layer_labels"] == ["vol-seller", "crash-hedge"]
    for key in ("stressed_pnl_correlation", "shared_tail_overlap", "factor_overlap"):
        matrix = block[key]
        assert len(matrix) == 2
        assert all(len(row) == 2 for row in matrix)
    assert len(block["marginal_risk_contribution"]) == 2


def test_decorrelation_realized_and_sharpe_are_gated_strings(
    compose_client: TestClient,
) -> None:
    block = _compose(compose_client, [_LAYER_A, _LAYER_B])["decorrelation"]
    realized = block["realized_correlation_unavailable_reason"]
    sharpe = block["marginal_sharpe_unavailable_reason"]
    assert isinstance(realized, str) and realized
    assert isinstance(sharpe, str) and sharpe
    assert "realized_correlation" not in block


def test_decorrelation_nan_serializes_as_json_null_not_string(
    compose_client: TestClient,
) -> None:
    response = compose_client.post(
        "/api/compose",
        json={"book_id": "BK-decorr", "trade_date": _TRADE.isoformat(), "layers": [_LAYER_A]},
    )
    assert response.status_code == 200, response.text
    raw = response.text
    assert "NaN" not in raw
    block = response.json()["decorrelation"]
    diagonal = block["stressed_pnl_correlation"][0][0]
    assert diagonal is None or isinstance(diagonal, float)
    for matrix_key in ("stressed_pnl_correlation", "shared_tail_overlap", "factor_overlap"):
        for row in block[matrix_key]:
            for cell in row:
                assert cell is None or (isinstance(cell, float) and math.isfinite(cell))
    assert "NaN" not in json.dumps(block)
