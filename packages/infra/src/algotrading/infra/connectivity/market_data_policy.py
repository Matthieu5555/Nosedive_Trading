"""Requested vs effective market-data capability, as a structured value.

A live feed can accept every subscription and still produce nothing — the account is not
entitled to that data, the broker silently downgraded the request, or it is pacing the
session. An operator should not have to infer that from log spam. This module turns it
into a value: a feed *notice* vocabulary (pacing / entitlement / other, the one place
that maps a broker's numeric error codes) and a :class:`MarketDataStatus` that pairs what
was *requested* against what was *effective* and against how many subscriptions are
actually *producing*.

It lives in ``connectivity`` rather than ``collectors`` so the broker adapter can classify
its own error events without a ``connectivity → collectors`` import cycle; ``collectors``
re-exports the notice vocabulary, so existing collector code is unchanged. Nothing here is
broker-specific: a broker adapter maps its native error code into a :class:`FeedNotice`
and reports its requested/observed market-data type; the assessment is the same for any
feed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

# Broker error codes that mean "you are requesting too fast" / pacing violations.
_PACING_CODES = frozenset({100, 420})
# Broker error codes that mean "you are not entitled to / not subscribed for this data".
# Includes the delayed-data downgrade notices a paper/unentitled login receives when it
# asks for live option data it cannot see (10089 / 10091), alongside the plain
# not-subscribed codes — every one of these means the live request did not take effect.
_ENTITLEMENT_CODES = frozenset({354, 10089, 10091, 10168, 10197})

PACING = "pacing"
ENTITLEMENT = "entitlement"
OTHER = "other"

# Market-data request/feed types, following the broker's numbering: 1 live, 2 frozen,
# 3 delayed, 4 delayed-frozen. 0 is the local "not yet observed" sentinel.
LIVE = 1
FROZEN = 2
DELAYED = 3
DELAYED_FROZEN = 4
UNKNOWN = 0

_MARKET_DATA_TYPE_NAMES = {
    LIVE: "live",
    FROZEN: "frozen",
    DELAYED: "delayed",
    DELAYED_FROZEN: "delayed-frozen",
    UNKNOWN: "unknown",
}


def market_data_type_name(market_data_type: int) -> str:
    """Human name for a market-data type integer (``"live"``, ``"delayed"``, …).

    Falls back to ``"type-<n>"`` for a code outside the known set rather than raising, so
    a diagnostic string is always renderable.
    """
    return _MARKET_DATA_TYPE_NAMES.get(market_data_type, f"type-{market_data_type}")


@dataclass(frozen=True, slots=True)
class FeedNotice:
    """One classified feed notice: its kind, the broker code, the message, and when."""

    kind: str
    code: int
    message: str
    ts: datetime


def classify_feed_notice(code: int, message: str, ts: datetime) -> FeedNotice:
    """Classify a broker notice code into the pacing/entitlement/other vocabulary."""
    if code in _PACING_CODES:
        kind = PACING
    elif code in _ENTITLEMENT_CODES:
        kind = ENTITLEMENT
    else:
        kind = OTHER
    return FeedNotice(kind=kind, code=code, message=message, ts=ts)


@dataclass(frozen=True, slots=True)
class MarketDataStatus:
    """What the feed actually delivered, against what was asked for.

    ``requested_type`` is the market-data type the session asked the broker for;
    ``effective_type`` is what the broker actually served (``UNKNOWN`` when no tick
    arrived to reveal it). ``subscribed`` is how many instruments were subscribed and
    ``producing`` how many yielded at least one observation. The notices are the
    classified feed events seen during the session. The point of the value is the gap it
    makes visible: ``subscribed`` high, ``producing`` zero, entitlement notices present —
    a clear entitlement failure rather than a quiet empty feed.
    """

    requested_type: int
    effective_type: int
    subscribed: int
    producing: int
    entitlement_notices: tuple[FeedNotice, ...]
    pacing_notices: tuple[FeedNotice, ...]

    @property
    def has_entitlement_problem(self) -> bool:
        """True when at least one entitlement notice was seen."""
        return bool(self.entitlement_notices)

    @property
    def is_producing(self) -> bool:
        """True when at least one subscribed instrument produced an observation."""
        return self.producing > 0

    @property
    def is_usable(self) -> bool:
        """True when the feed is actionable: something was subscribed and is producing."""
        return self.subscribed > 0 and self.producing > 0

    @property
    def downgraded(self) -> bool:
        """True when the effective type is known and differs from what was requested.

        A request for ``live`` served as ``delayed`` is a downgrade worth surfacing even
        when data is flowing.
        """
        return self.effective_type != UNKNOWN and self.effective_type != self.requested_type

    def describe(self) -> str:
        """One operator-facing line explaining the feed's state and likely cause.

        Names the requested vs effective type, the subscribed/producing counts, and — when
        the feed produced nothing — the most likely reason (entitlement codes if any, else
        a generic empty-feed note). Deterministic: codes are listed in sorted order.
        """
        parts = [
            f"requested {market_data_type_name(self.requested_type)}, "
            f"effective {market_data_type_name(self.effective_type)}",
            f"subscribed {self.subscribed}, producing {self.producing}",
        ]
        if self.has_entitlement_problem:
            distinct_codes = sorted({notice.code for notice in self.entitlement_notices})
            codes = ", ".join(str(code) for code in distinct_codes)
            parts.append(f"entitlement notices (codes {codes})")
        if self.pacing_notices:
            parts.append(f"{len(self.pacing_notices)} pacing notice(s)")
        if not self.is_producing:
            if self.has_entitlement_problem:
                parts.append("no data is flowing — the live request was not entitled/effective")
            else:
                parts.append("no data is flowing — market closed, thin feed, or wrong session")
        elif self.downgraded:
            parts.append("data is flowing but downgraded from the requested type")
        return "; ".join(parts)


def assess_market_data(
    *,
    requested_type: int,
    effective_type: int,
    subscribed: int,
    producing: int,
    notices: Sequence[FeedNotice],
) -> MarketDataStatus:
    """Assemble a :class:`MarketDataStatus` from a session's types, counts, and notices.

    Pure: it partitions the classified notices into entitlement and pacing buckets and
    records the counts. The caller supplies ``subscribed``/``producing`` (e.g. from the
    collector's daily summary) and the requested/observed types from the session; this
    function adds no I/O and reads no clock.
    """
    entitlement = tuple(notice for notice in notices if notice.kind == ENTITLEMENT)
    pacing = tuple(notice for notice in notices if notice.kind == PACING)
    return MarketDataStatus(
        requested_type=requested_type,
        effective_type=effective_type,
        subscribed=subscribed,
        producing=producing,
        entitlement_notices=entitlement,
        pacing_notices=pacing,
    )
