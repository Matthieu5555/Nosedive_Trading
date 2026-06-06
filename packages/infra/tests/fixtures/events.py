"""Raw market-event fixtures for the snapshot builder (Workstream C extends here).

The chain fixtures in :mod:`fixtures.library` are quote-level — one bid/ask/last
per instrument at one instant. The snapshot builder, though, reads *field-level*
:class:`~contracts.RawMarketEvent` records, each with its own ``canonical_ts``, so
the look-ahead boundary, the staleness threshold, and the labeled price fallbacks
each need raw events timed deliberately. Those live here as named builders and
scenarios, so the snapshot edge-case tests bind to one curated home instead of
inventing inline literals (TESTING.md).

``SNAPSHOT_TS`` mirrors ``fixtures.library.AS_OF`` and ``STALE_THRESHOLD_SECONDS``
mirrors ``configs/qc.yaml``'s ``max_quote_age_seconds`` so staleness fixtures
line up with the default config a test would load.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from algotrading.infra.contracts import InstrumentKey, RawMarketEvent

from .library import make_option, make_underlying

SNAPSHOT_TS = datetime(2026, 5, 29, 15, 30, 0, tzinfo=UTC)
STALE_THRESHOLD_SECONDS = 30.0

SESSION_ID = "sess-snapshot"
UNDERLYING = make_underlying("AAPL")
OPTION = make_option("AAPL", 100.0, "C", date(2026, 6, 19))


def event(
    instrument: InstrumentKey,
    field_name: str,
    value: float,
    *,
    ts: datetime,
    session_id: str = SESSION_ID,
    event_id: str | None = None,
) -> RawMarketEvent:
    """Build one raw market event for ``instrument``'s ``field_name`` at ``ts``.

    The three timestamps collapse to ``ts`` (the cases here exercise ordering and
    staleness, not the exchange/receipt skew), and the partition fields are derived
    from the instrument and the timestamp the same way the live feed would.
    """
    key = instrument.canonical()
    resolved_id = event_id if event_id is not None else f"{field_name}@{ts.isoformat()}"
    return RawMarketEvent(
        session_id=session_id,
        event_id=resolved_id,
        instrument_key=key,
        exchange_ts=ts,
        receipt_ts=ts,
        canonical_ts=ts,
        field_name=field_name,
        value=value,
        trade_date=ts.date(),
        underlying=instrument.underlying_symbol,
    )


def quote_events(
    instrument: InstrumentKey,
    *,
    bid: float | None = None,
    ask: float | None = None,
    last: float | None = None,
    ts: datetime = SNAPSHOT_TS,
    session_id: str = SESSION_ID,
) -> tuple[RawMarketEvent, ...]:
    """Build the bid/ask/last events present for one instrument at one instant."""
    fields = (("bid", bid), ("ask", ask), ("last", last))
    return tuple(
        event(instrument, name, value, ts=ts, session_id=session_id)
        for name, value in fields
        if value is not None
    )


def boundary_bid_events() -> tuple[RawMarketEvent, ...]:
    """Three bids: before, exactly at, and just after ``SNAPSHOT_TS``.

    The middle bid (190.5) is timestamped exactly at the snapshot instant and must
    be the one chosen; the last (191.0) is one second later and must never leak in.
    """
    return (
        event(UNDERLYING, "bid", 190.0, ts=SNAPSHOT_TS - timedelta(seconds=5), event_id="b-early"),
        event(UNDERLYING, "bid", 190.5, ts=SNAPSHOT_TS, event_id="b-at"),
        event(UNDERLYING, "bid", 191.0, ts=SNAPSHOT_TS + timedelta(seconds=1), event_id="b-after"),
    )


def threshold_straddle_events() -> tuple[RawMarketEvent, ...]:
    """A clean two-sided quote whose age sits exactly on the staleness threshold.

    The events are stamped ``STALE_THRESHOLD_SECONDS`` before ``SNAPSHOT_TS``, so an
    age strictly greater than the threshold is just-over and equal is exactly-at.
    """
    at_threshold = SNAPSHOT_TS - timedelta(seconds=STALE_THRESHOLD_SECONDS)
    return quote_events(UNDERLYING, bid=190.4, ask=190.6, last=190.5, ts=at_threshold)


def crossed_then_last_events() -> tuple[RawMarketEvent, ...]:
    """A crossed quote (bid 190.6 > ask 190.4) plus a usable last trade (190.5).

    The crossed bid/ask must be rejected from the mid and the reference spot must
    fall back to the last, labeled as such — never a silent crossed-mid.
    """
    return quote_events(UNDERLYING, bid=190.6, ask=190.4, last=190.5)


def single_last_event() -> tuple[RawMarketEvent, ...]:
    """Only a last trade — no two-sided quote, so the spot falls back to last."""
    return (event(UNDERLYING, "last", 190.5, ts=SNAPSHOT_TS),)


def single_bid_event() -> tuple[RawMarketEvent, ...]:
    """Only a one-sided bid — no mid, no last, no fallback: insufficient data."""
    return (event(UNDERLYING, "bid", 190.4, ts=SNAPSHOT_TS),)
