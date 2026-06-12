"""Typed CP REST wire models — the verbatim coercers and the per-shape models.

These pin the *scalar* coercion contracts the wire models carry (the parsers M4 moved verbatim
out of the normalizer / close capture / history normalizer) plus the per-row skip semantics.
Expected values are hand-derived from the CP Web API conventions: field values are strings,
optionally prefixed with one status flag (``C`` prior close, ``H`` halted); ``-1`` is the
"no value" sentinel; conids/timestamps are integers riding an untyped JSON payload.
"""

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
        ("9.27", 9.27),  # plain string-typed quote
        ("C189.5", 189.5),  # 'C' = prior-close flag, stripped
        ("H12.0", 12.0),  # 'H' = halted flag, stripped
        ("  10 ", 10.0),  # whitespace tolerated
        (9.27, 9.27),  # already numeric
        ("-1", None),  # IBKR's no-value sentinel
        (-1, None),
        ("-0.5", -0.5),  # a genuine negative is NOT the sentinel
        (None, None),
        ("", None),
        ("C", None),  # a flag with no number behind it
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
        ("  42 ", 42),  # whitespace-padded broker string
        (7.0, 7),  # float-typed integer truncates (int())
        (True, None),  # JSON true is never a conid/timestamp
        (False, None),
        (None, None),
        ("abc", None),
        (float("nan"), None),
        ({}, None),  # a nested object is not a scalar
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
            "31": "-1",  # sentinel → None
            "55": "SPY",  # unknown tag → ignored
            "server_id": "q0",
        }
    )
    assert row.conid == 265598
    assert row.updated_ms == 1_717_525_760_115
    assert row.bid == 9.27
    assert row.ask == 9.31  # status flag stripped
    assert row.last is None  # sentinel dropped
    assert row.bid_size is None  # absent tag


def test_snapshot_row_warmth_is_the_normalizers_parse() -> None:
    # A metadata-only row is cold; so is a row carrying ONLY the -1 sentinel (it would emit zero
    # events — the divergence the old lstrip("CHch") warm-check had); one parseable tag is warm.
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
    # 84=bid, 86=ask, 88=bid_size, 85=ask_size, 31=last, 7059=last_size (CP field codes).
    assert SNAPSHOT_FIELD_TAGS == ("84", "86", "88", "85", "31", "7059")


def test_secdef_search_rows_skip_garbage_and_coerce_string_conids() -> None:
    rows = parse_secdef_search_rows(
        [
            {"conid": "4762", "symbol": "BA", "sections": [{"secType": "STK"}, "junk"]},
            "not-a-row",
            {"conid": "not-a-conid", "symbol": "BA"},  # uncoercible conid → row skipped
            {"symbol": "BA"},  # no conid is still a row (callers filter)
        ]
    )
    assert [row.conid for row in rows] == [4762, None]
    assert rows[0].sections[0].sec_type == "STK"  # non-mapping section entry skipped
    assert parse_secdef_search_rows({"not": "a list"}) == ()


def test_history_bar_row_rejects_dishonest_numbers() -> None:
    good = {"t": 1_717_459_200_000, "o": 99.0, "h": 101.5, "l": 98.5, "c": 100.25, "v": 1}
    bar = HistoryBarRow.model_validate(good)
    assert (bar.open_price, bar.high, bar.low, bar.close, bar.volume) == (
        99.0, 101.5, 98.5, 100.25, 1.0,
    )
    # A string-typed number is rejected (the normalize door refuses to coerce), as are bools,
    # non-finite values, and a missing field — each naming the wire field code.
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
