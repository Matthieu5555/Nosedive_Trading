from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

_PACING_CODES = frozenset({100, 420})
_ENTITLEMENT_CODES = frozenset({354, 10089, 10091, 10168, 10197})

PACING = "pacing"
ENTITLEMENT = "entitlement"
OTHER = "other"

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
    return _MARKET_DATA_TYPE_NAMES.get(market_data_type, f"type-{market_data_type}")


@dataclass(frozen=True, slots=True)
class FeedNotice:

    kind: str
    code: int
    message: str
    ts: datetime


def classify_feed_notice(code: int, message: str, ts: datetime) -> FeedNotice:
    if code in _PACING_CODES:
        kind = PACING
    elif code in _ENTITLEMENT_CODES:
        kind = ENTITLEMENT
    else:
        kind = OTHER
    return FeedNotice(kind=kind, code=code, message=message, ts=ts)


@dataclass(frozen=True, slots=True)
class MarketDataStatus:

    requested_type: int
    effective_type: int
    subscribed: int
    producing: int
    entitlement_notices: tuple[FeedNotice, ...]
    pacing_notices: tuple[FeedNotice, ...]

    @property
    def has_entitlement_problem(self) -> bool:
        return bool(self.entitlement_notices)

    @property
    def is_producing(self) -> bool:
        return self.producing > 0

    @property
    def is_usable(self) -> bool:
        return self.subscribed > 0 and self.producing > 0

    @property
    def downgraded(self) -> bool:
        return self.effective_type != UNKNOWN and self.effective_type != self.requested_type

    def describe(self) -> str:
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
