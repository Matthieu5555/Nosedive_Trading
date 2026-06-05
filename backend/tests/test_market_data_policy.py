"""Tests for `connectivity.market_data_policy` — the feed entitlement/status value.

Covers the notice classification (including the delayed-data entitlement codes a paper
login receives) and the :class:`MarketDataStatus` assembled from a session's types,
counts, and notices. Expected classifications are derived from the documented broker code
buckets, not read back from the function under test.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from connectivity import (
    ENTITLEMENT,
    OTHER,
    PACING,
    assess_market_data,
    classify_feed_notice,
    market_data_type_name,
)
from connectivity.market_data_policy import DELAYED, LIVE, UNKNOWN

_TS = datetime(2026, 6, 5, 14, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("code", "expected_kind"),
    [
        (100, PACING),
        (420, PACING),
        (354, ENTITLEMENT),
        (10089, ENTITLEMENT),   # additional-subscription / delayed-data downgrade
        (10091, ENTITLEMENT),   # not subscribed; displaying delayed
        (10168, ENTITLEMENT),
        (10197, ENTITLEMENT),
        (2104, OTHER),          # "market data farm connection is OK" — benign info
        (200, OTHER),           # no security definition — not a feed-health notice
    ],
)
def test_classify_feed_notice_buckets_codes(code: int, expected_kind: str) -> None:
    notice = classify_feed_notice(code, "msg", _TS)
    assert notice.kind == expected_kind
    assert notice.code == code
    assert notice.ts == _TS


@pytest.mark.parametrize(
    ("market_data_type", "expected"),
    [
        (1, "live"), (2, "frozen"), (3, "delayed"),
        (4, "delayed-frozen"), (0, "unknown"), (9, "type-9"),
    ],
)
def test_market_data_type_name(market_data_type: int, expected: str) -> None:
    assert market_data_type_name(market_data_type) == expected


def test_assess_market_data_partitions_notices_and_counts() -> None:
    notices = [
        classify_feed_notice(10091, "not subscribed; delayed", _TS),
        classify_feed_notice(420, "pacing violation", _TS),
        classify_feed_notice(2104, "farm OK", _TS),
    ]
    status = assess_market_data(
        requested_type=LIVE, effective_type=DELAYED, subscribed=1095, producing=0, notices=notices
    )
    assert len(status.entitlement_notices) == 1
    assert status.entitlement_notices[0].code == 10091
    assert len(status.pacing_notices) == 1
    assert status.has_entitlement_problem is True
    assert status.is_producing is False
    assert status.is_usable is False
    # effective (delayed) != requested (live), so it is also a downgrade.
    assert status.downgraded is True


def test_describe_names_the_entitlement_failure_when_nothing_produces() -> None:
    notices = [classify_feed_notice(10091, "not subscribed", _TS)]
    status = assess_market_data(
        requested_type=LIVE, effective_type=DELAYED, subscribed=1095, producing=0, notices=notices
    )
    described = status.describe()
    assert "requested live, effective delayed" in described
    assert "subscribed 1095, producing 0" in described
    assert "10091" in described
    assert "not entitled" in described


def test_usable_feed_with_no_notices_reads_clean() -> None:
    status = assess_market_data(
        requested_type=DELAYED, effective_type=DELAYED, subscribed=10, producing=10, notices=[]
    )
    assert status.is_usable is True
    assert status.has_entitlement_problem is False
    assert status.downgraded is False
    assert "producing 10" in status.describe()


def test_unknown_effective_type_is_not_a_downgrade() -> None:
    # No tick arrived to reveal the served type; an unknown effective type must not be
    # reported as a downgrade against the request.
    status = assess_market_data(
        requested_type=LIVE, effective_type=UNKNOWN, subscribed=5, producing=0, notices=[]
    )
    assert status.downgraded is False
