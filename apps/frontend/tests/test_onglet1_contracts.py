from __future__ import annotations

import math
from datetime import UTC, date, datetime
from pathlib import Path
from types import ModuleType

import pytest
from algotrading.core.provenance import stamp
from algotrading.frontend.app import create_app
from algotrading.frontend.context import AppContext
from algotrading.frontend.serializers import (
    FORWARD_RATE_UNIT,
    OptionQuote,
    _quote_to_dict,
    forward_rate_diagnostics_to_dict,
    projected_option_analytics_to_dict,
)
from algotrading.infra.contracts import tables
from algotrading.infra.storage import ParquetStore
from fastapi.testclient import TestClient

# ----------------------------------------------------------------------------------------------
# Onglet-1 BFF read contracts. These lock the seams that bit us this wave: a renamed/shifted-shape
# field silently broke the price-structure block (a flat `bid` where the contract says nested
# `quote.bid`). Each test is named so its failure message says what shifted; expected values are
# derived here by hand, never copied from the code under test.
# ----------------------------------------------------------------------------------------------

_AS_OF = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)
_EXPIRY = date(2026, 8, 28)


def _prov():
    return stamp(
        calc_ts=_AS_OF,
        code_version="contract-test",
        config_hashes={"cfg": "contract"},
        source_records=(),
        source_timestamps=(),
    )


def _analytics_row(seed: ModuleType) -> tables.ProjectedOptionAnalytics:
    return seed.analytics_cell(
        delta_band="30dp",
        target_delta=-0.30,
        log_moneyness=-0.15,
        implied_vol=0.27,
        delta=-0.30,
        dollar_delta=-58.5,
    )


# --- SEAM 1: the analytics cell quote object -------------------------------------------------
# The price-structure block reads `point.quote.bid` / `.ask` / `.volume`. The bug class was a
# field that lived at a different path (flat `bid`) or vanished when a quote was absent. The
# contract: every cell carries a nested `quote` object that is ALWAYS present; its values are
# nullable. These pin the PATH, not just the presence.


def test_quote_path_is_nested_object_not_flat_bid_ask_volume(seed: ModuleType) -> None:
    quote = OptionQuote(bid=4.10, ask=4.40, volume=1875.0)
    cell = projected_option_analytics_to_dict(_analytics_row(seed), quote)

    assert "quote" in cell, (
        "FIELD PATH SHIFTED: the analytics cell must carry a nested 'quote' object; "
        "the price-structure block reads point.quote.bid"
    )
    assert set(cell["quote"]) == {"bid", "ask", "volume"}, (
        "quote object shape shifted: expected exactly {bid, ask, volume}"
    )
    # The exact seam that broke: bid/ask/volume must NOT be flattened onto the cell root.
    for flat in ("bid", "ask", "volume"):
        assert flat not in cell, (
            f"FIELD PATH SHIFTED: '{flat}' must live at quote.{flat}, never flat on the cell root"
        )


def test_quote_object_present_with_two_sided_values(seed: ModuleType) -> None:
    # Hand-set two-sided quote; the serializer surfaces it verbatim (no recompute).
    quote = OptionQuote(bid=4.10, ask=4.40, volume=1875.0)
    cell = projected_option_analytics_to_dict(_analytics_row(seed), quote)
    assert cell["quote"] == {"bid": 4.10, "ask": 4.40, "volume": 1875.0}


def test_quote_object_present_with_all_null_when_quote_absent(seed: ModuleType) -> None:
    # No quote threaded onto the cell -> the object is STILL present, all three values null. This
    # is the absent-quote case the price-structure block renders as "—".
    cell = projected_option_analytics_to_dict(_analytics_row(seed), None)
    assert cell["quote"] == {"bid": None, "ask": None, "volume": None}, (
        "quote must stay present-with-null when no quote was matched, never omitted"
    )


def test_quote_to_dict_never_returns_none(seed: ModuleType) -> None:
    # The helper itself: a None quote serializes to the all-null object, never to None/omitted.
    assert _quote_to_dict(None) == {"bid": None, "ask": None, "volume": None}
    assert _quote_to_dict(OptionQuote(bid=1.0, ask=None, volume=2.0)) == {
        "bid": 1.0,
        "ask": None,
        "volume": 2.0,
    }


# --- SEAM 2: maturities[].rate_diagnostics identity (A5) -------------------------------------
# Each maturity entry carries `rate_diagnostics` = {forward_price, implied_rate, implied_carry,
# implied_dividend, rate_unit} OR null. Lock the identity implied_dividend == implied_rate -
# implied_carry, the rate_unit string, and the field shape.


def _forward_point(
    *, implied_rate: float | None, implied_carry: float | None, implied_dividend: float | None
) -> tables.ForwardCurvePoint:
    return tables.ForwardCurvePoint(
        snapshot_ts=_AS_OF,
        underlying="AAA",
        maturity_years=0.25,
        expiry_date=_EXPIRY,
        day_count="ACT/365",
        forward_price=195.0,
        diagnostics=tables.ForwardDiagnostics(
            method="parity", candidate_count=5, residual_mad=0.01, quality_label="good"
        ),
        source_snapshot_ts=_AS_OF,
        provenance=_prov(),
        implied_rate=implied_rate,
        implied_carry=implied_carry,
        implied_dividend=implied_dividend,
    )


def test_rate_diagnostics_shape_and_unit_string() -> None:
    # Eq 5 hand value: r=0.04, F=195, S=192, T=0.25 -> carry = ln(F/S)/T, dividend = r - carry.
    rate = 0.04
    carry = math.log(195.0 / 192.0) / 0.25
    dividend = rate - carry
    diag = forward_rate_diagnostics_to_dict(
        _forward_point(implied_rate=rate, implied_carry=carry, implied_dividend=dividend)
    )
    assert set(diag) == {
        "forward_price",
        "implied_rate",
        "implied_carry",
        "implied_dividend",
        "rate_unit",
    }, "rate_diagnostics shape shifted: a field was renamed/added/dropped"
    assert diag["rate_unit"] == "/yr (annualized, continuous)"
    assert diag["rate_unit"] == FORWARD_RATE_UNIT, "rate_unit string shifted from its constant"
    assert diag["forward_price"] == pytest.approx(195.0)
    assert diag["implied_rate"] == pytest.approx(rate)
    assert diag["implied_carry"] == pytest.approx(carry)
    assert diag["implied_dividend"] == pytest.approx(dividend)


def test_rate_diagnostics_dividend_equals_rate_minus_carry_identity() -> None:
    # The decomposition identity must round-trip through the serializer verbatim (no recompute).
    rate = 0.031
    carry = math.log(200.0 / 195.0) / 0.5
    dividend = rate - carry
    diag = forward_rate_diagnostics_to_dict(
        _forward_point(implied_rate=rate, implied_carry=carry, implied_dividend=dividend)
    )
    assert diag["implied_dividend"] == pytest.approx(
        diag["implied_rate"] - diag["implied_carry"], abs=1e-12
    ), "RATE IDENTITY BROKEN: implied_dividend must equal implied_rate - implied_carry"


def test_rate_diagnostics_present_when_forward_point_seeded(
    tmp_path: Path, seed: ModuleType
) -> None:
    rate = 0.04
    carry = math.log(195.0 / 192.0) / 0.25
    root = tmp_path / "data"
    seed.seed_store(root)
    store = ParquetStore(root)
    store.write(
        "forward_curve",
        [_forward_point(implied_rate=rate, implied_carry=carry, implied_dividend=rate - carry)],
    )
    ctx = AppContext(
        store_root=root,
        configs_dir=tmp_path / "configs",
        store=ParquetStore(root),
        default_underlying=seed.MEMBER_AAA,
    )
    with TestClient(create_app(ctx)) as client:
        maturity = client.get(
            "/api/analytics",
            params={"underlying": seed.MEMBER_AAA, "trade_date": seed.TRADE_DATE.isoformat()},
        ).json()["maturities"][0]
    assert maturity["rate_diagnostics"] is not None
    assert maturity["rate_diagnostics"]["implied_dividend"] == pytest.approx(
        maturity["rate_diagnostics"]["implied_rate"]
        - maturity["rate_diagnostics"]["implied_carry"]
    )


def test_rate_diagnostics_is_null_when_no_forward_point(
    tmp_path: Path, seed: ModuleType
) -> None:
    # The seeded store banks no forward_curve for MEMBER_AAA -> the key is present and null, never
    # omitted (so the front can branch on null rather than crash on a missing key).
    root = tmp_path / "data"
    seed.seed_store(root)
    ctx = AppContext(
        store_root=root,
        configs_dir=tmp_path / "configs",
        store=ParquetStore(root),
        default_underlying=seed.MEMBER_AAA,
    )
    with TestClient(create_app(ctx)) as client:
        maturity = client.get(
            "/api/analytics",
            params={"underlying": seed.MEMBER_AAA, "trade_date": seed.TRADE_DATE.isoformat()},
        ).json()["maturities"][0]
    assert "rate_diagnostics" in maturity, "rate_diagnostics key must be present even when null"
    assert maturity["rate_diagnostics"] is None


# --- SEAM 3: ForwardCurvePoint additive-nullable round-trip ----------------------------------
# The three additive-nullable fields implied_rate / implied_carry / implied_dividend must
# round-trip through forward_rate_diagnostics_to_dict, and stay null when the contract carries
# null (a point that predates the additive fields).


@pytest.mark.parametrize(
    ("rate", "carry", "dividend"),
    [
        pytest.param(0.04, 0.062, -0.022, id="all-populated"),
        pytest.param(None, None, None, id="all-null-legacy-point"),
        pytest.param(0.03, None, None, id="rate-only"),
    ],
)
def test_forward_curve_additive_fields_round_trip(
    rate: float | None, carry: float | None, dividend: float | None
) -> None:
    diag = forward_rate_diagnostics_to_dict(
        _forward_point(implied_rate=rate, implied_carry=carry, implied_dividend=dividend)
    )
    assert diag["implied_rate"] == (pytest.approx(rate) if rate is not None else None)
    assert diag["implied_carry"] == (pytest.approx(carry) if carry is not None else None)
    assert diag["implied_dividend"] == (
        pytest.approx(dividend) if dividend is not None else None
    )
