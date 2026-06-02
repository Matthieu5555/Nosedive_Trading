"""Classify a broker feed notice as pacing, entitlement, or other.

The spec asks the collector to *detect pacing/entitlement failures and log them as
structured events* — distinct from a missing data interval, which is a durable gap
event. A notice is classified into a small broker-agnostic vocabulary (the broker's
own numeric error codes are mapped here, the one place that knows them) and counted in
the daily summary, so feed-health problems are visible without polluting the raw
observation stream.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# Broker error codes that mean "you are requesting too fast" / pacing violations.
_PACING_CODES = frozenset({100, 420})
# Broker error codes that mean "you are not entitled to / not subscribed for this data".
_ENTITLEMENT_CODES = frozenset({354, 10168, 10197})

PACING = "pacing"
ENTITLEMENT = "entitlement"
OTHER = "other"


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
