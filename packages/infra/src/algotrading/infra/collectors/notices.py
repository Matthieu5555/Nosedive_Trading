"""Re-export the broker-neutral feed-notice vocabulary (now homed in connectivity).

Feed-notice classification — mapping a broker's numeric error codes into the
pacing/entitlement/other vocabulary — moved down to
:mod:`connectivity.market_data_policy` so the broker adapter can classify its own error
events without a ``connectivity → collectors`` import cycle. The collector still detects
pacing/entitlement failures and counts them in the daily summary, distinct from a missing
data interval (a durable gap event); it just shares one classifier with the adapter now.

This module re-exports the same names so existing collector code and tests are unchanged.
"""

from __future__ import annotations

from algotrading.infra.connectivity.market_data_policy import (
    ENTITLEMENT,
    OTHER,
    PACING,
    FeedNotice,
    classify_feed_notice,
)

__all__ = [
    "ENTITLEMENT",
    "OTHER",
    "PACING",
    "FeedNotice",
    "classify_feed_notice",
]
