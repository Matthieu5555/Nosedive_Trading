from __future__ import annotations

import math

import pytest
from algotrading.infra_ibkr.collectors.cp_rest_wire import (
    SNAPSHOT_FIELD_TAGS,
    HistoryBarRow,
    SnapshotRow,
    coerce_int_or_none,
    parse_field_value,
    parse_secdef_search_rows,
    parse_snapshot_rows,
)
from pydantic import ValidationError


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("9.27", 9.27),
        ("C189.5", 189.5),
        ("H12.0", 12.0),
        ("  10 ", 10.0),
        (9.27, 9.27),
        ("-1", None),
        (-1, None),
        ("-0.5", -0.5),
        (None, None),
        ("", None),
        ("C", None),
        ("garbage", None),
        (float("nan"), None),
        (float("inf"), None),
    ],
)
def test_parse_field_value(raw: object, expected: float | None) -> None:
    assert parse_field_value(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (416904, 416904),
        ("416904", 416904),
        ("  42 ", 42),
        (7.0, 7),
        (True, None),
        (False, None),
        (None, None),
        ("abc", None),
        (float("nan"), None),
        ({}, None),
    ],
)
def test_coerce_int_or_none(raw: object, expected: int | None) -> None:
    assert coerce_int_or_none(raw) == expected


def test_snapshot_row_parses_tags_and_ignores_extras() -> None:
    row = SnapshotRow.model_validate(
        {
            "conid": "265598",
            "_updated": 1_717_525_760_115,
            "84": "9.27",
            "86": "C9.31",
            "31": "-1",
            "55": "SPY",
            "server_id": "q0",
        }
    )
    assert row.conid == 265598
    assert row.updated_ms == 1_717_525_760_115
    assert row.bid == 9.27
    assert row.ask == 9.31
    assert row.last is None
    assert row.bid_size is None


def test_snapshot_row_warmth_is_the_normalizers_parse() -> None:
    assert not SnapshotRow.model_validate({"conid": 1, "server_id": "q0"}).has_market_value()
    assert not SnapshotRow.model_validate({"conid": 1, "31": "-1"}).has_market_value()
    assert SnapshotRow.model_validate({"conid": 1, "31": "C9.29"}).has_market_value()


def test_snapshot_row_spot_reads_last_then_bid_then_ask_positive_only() -> None:
    assert SnapshotRow.model_validate({"31": "100.0", "84": "99.0"}).spot_value() == 100.0
    assert SnapshotRow.model_validate({"84": "99.0", "86": "101.0"}).spot_value() == 99.0
    assert SnapshotRow.model_validate({"31": "0", "86": "101.0"}).spot_value() == 101.0
    assert SnapshotRow.model_validate({"31": "-5"}).spot_value() is None


def test_parse_snapshot_rows_skips_non_rows() -> None:
    rows = parse_snapshot_rows([{"conid": 1, "84": "1.0"}, "junk", 7, {"conid": 2}])
    assert [row.conid for row in rows] == [1, 2]
    assert parse_snapshot_rows("not-a-list") == ()
    assert parse_snapshot_rows(None) == ()


def test_snapshot_field_tags_are_the_cp_codes() -> None:
    assert SNAPSHOT_FIELD_TAGS == ("84", "86", "88", "85", "31", "7059", "7762")


def test_secdef_search_rows_skip_garbage_and_coerce_string_conids() -> None:
    rows = parse_secdef_search_rows(
        [
            {"conid": "4762", "symbol": "BA", "sections": [{"secType": "STK"}, "junk"]},
            "not-a-row",
            {"conid": "not-a-conid", "symbol": "BA"},
            {"symbol": "BA"},
        ]
    )
    assert [row.conid for row in rows] == [4762, None]
    assert rows[0].sections[0].sec_type == "STK"
    assert parse_secdef_search_rows({"not": "a list"}) == ()


def test_history_bar_row_rejects_dishonest_numbers() -> None:
    good = {"t": 1_717_459_200_000, "o": 99.0, "h": 101.5, "l": 98.5, "c": 100.25, "v": 1}
    bar = HistoryBarRow.model_validate(good)
    assert (bar.open_price, bar.high, bar.low, bar.close, bar.volume) == (
        99.0, 101.5, 98.5, 100.25, 1.0,
    )
    for bad, code in [
        ({**good, "c": "100.25"}, "c"),
        ({**good, "v": True}, "v"),
        ({**good, "h": math.nan}, "h"),
    ]:
        with pytest.raises(ValidationError) as excinfo:
            HistoryBarRow.model_validate(bad)
        assert excinfo.value.errors()[0]["loc"] == (code,)
    with pytest.raises(ValidationError) as excinfo:
        HistoryBarRow.model_validate({k: v for k, v in good.items() if k != "c"})
    first = excinfo.value.errors()[0]
    assert first["type"] == "missing" and first["loc"] == ("c",)
