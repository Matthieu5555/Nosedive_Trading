"""Normalize an IBKR Client Portal ``marketdata/history`` payload into ``DailyBar`` rows.

ADR 0031: the historical backfill fetches daily OHLC over ``GET
/iserver/marketdata/history`` (``bar=1d``) and lands each bar in the immutable
``DailyBar`` table (ADR 0019/0034 §4, provider-partitioned). This is the history twin of
:mod:`.cp_rest_normalize` (which maps live snapshot rows to ``RawMarketEvent``): one pure,
SDK-free function from a captured payload to typed contracts, fully exercised in CI.

The Client Portal history payload shape (per the CP Web API docs):

    {"symbol": "AAPL", "data": [
        {"t": 1716940800000, "o": 99.0, "h": 101.5, "l": 98.5, "c": 100.25, "v": 1234567},
        ...
    ]}

Each ``data`` row is one bar: ``t`` is the bar's start time in epoch **milliseconds** (UTC),
``o/h/l/c`` the OHLC prices, ``v`` the volume. The ``t`` → ``trade_date`` mapping is the
load-bearing, look-ahead-sensitive step: a bar is stamped with **its own** trade date, never
a later one, so a backfill never writes a future-dated value onto a past bar.

A row that cannot be turned into an honest bar (missing field, non-finite, ``high < low``,
open/close outside ``[low, high]``) is rejected with a labeled error rather than coerced —
the same write-ahead discipline storage enforces, applied at the normalize door so a bad
fetch fails before it reaches disk.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime

from algotrading.core.provenance import ProvenanceStamp
from algotrading.infra.contracts import DailyBar

# CP history row field codes → meaning. ``t`` epoch-ms (UTC), ``o/h/l/c`` prices, ``v`` volume.
_TIME_MS = "t"
_OPEN = "o"
_HIGH = "h"
_LOW = "l"
_CLOSE = "c"
_VOLUME = "v"


class HistoryNormalizeError(Exception):
    """A history payload/row could not be turned into an honest ``DailyBar`` — labeled."""


def _require_number(row: Mapping[str, object], key: str) -> float:
    if key not in row:
        raise HistoryNormalizeError(f"history bar missing field {key!r}: {row!r}")
    value = row[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HistoryNormalizeError(f"history bar field {key!r} must be numeric, got {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise HistoryNormalizeError(f"history bar field {key!r} is not finite: {value!r}")
    return number


def trade_date_of_bar(epoch_ms: object) -> date:
    """Map a CP history bar's epoch-millisecond timestamp to its UTC trade date.

    The bar's ``t`` is its session timestamp in UTC milliseconds; the trade date is that
    instant's UTC calendar date. This is the no-look-ahead anchor: the date comes from the
    bar's *own* timestamp, so a bar is never stamped with a date from after its session.
    """
    if isinstance(epoch_ms, bool) or not isinstance(epoch_ms, (int, float)):
        raise HistoryNormalizeError(f"history bar timestamp must be numeric, got {epoch_ms!r}")
    if not math.isfinite(float(epoch_ms)):
        raise HistoryNormalizeError(f"history bar timestamp is not finite: {epoch_ms!r}")
    return datetime.fromtimestamp(float(epoch_ms) / 1000.0, tz=UTC).date()


def _row_to_bar(
    row: Mapping[str, object],
    *,
    provider: str,
    underlying: str,
    bar_type: str,
    source: str,
    provenance: ProvenanceStamp,
) -> DailyBar:
    if _TIME_MS not in row:
        raise HistoryNormalizeError(f"history bar missing timestamp {_TIME_MS!r}: {row!r}")
    high = _require_number(row, _HIGH)
    low = _require_number(row, _LOW)
    open_ = _require_number(row, _OPEN)
    close = _require_number(row, _CLOSE)
    volume = _require_number(row, _VOLUME)
    # Reject inconsistent OHLC at the normalize door (mirrors storage's write-ahead check),
    # so a corrupt fetch fails here with the offending field named rather than at the write.
    if high < low:
        raise HistoryNormalizeError(f"history bar high {high!r} < low {low!r}: {row!r}")
    for name, value in (("open", open_), ("close", close)):
        if not (low <= value <= high):
            raise HistoryNormalizeError(
                f"history bar {name} {value!r} outside [low={low!r}, high={high!r}]: {row!r}"
            )
    if volume < 0:
        raise HistoryNormalizeError(f"history bar volume must be non-negative: {volume!r}")
    return DailyBar(
        provider=provider,
        underlying=underlying,
        trade_date=trade_date_of_bar(row[_TIME_MS]),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        bar_type=bar_type,
        source=source,
        provenance=provenance,
    )


def history_to_daily_bars(
    payload: Mapping[str, object],
    *,
    provider: str,
    underlying: str,
    bar_type: str,
    source: str,
    provenance_for: object,
) -> tuple[DailyBar, ...]:
    """Normalize a CP ``marketdata/history`` payload into a tuple of ``DailyBar`` rows.

    ``payload`` is the decoded JSON body; its ``data`` list holds one row per daily bar.
    Each row is mapped to a :class:`DailyBar` keyed by ``(provider, underlying, trade_date)``
    (the bar's own timestamp gives the trade date — no look-ahead). ``provenance_for`` is a
    callable ``(trade_date) -> ProvenanceStamp`` so each bar carries a stamp naming its own
    source/lineage and a per-day ``calc_ts``.

    An empty window (no ``data`` rows) yields an empty tuple — a window with no history is a
    valid answer, not an error. A malformed row raises :class:`HistoryNormalizeError`. Output
    order follows the payload's row order (CP returns bars chronologically).
    """
    data = payload.get("data")
    if data is None:
        return ()
    if not isinstance(data, Sequence) or isinstance(data, (str, bytes)):
        raise HistoryNormalizeError(f"history payload 'data' must be a list, got {data!r}")
    bars: list[DailyBar] = []
    for row in data:
        if not isinstance(row, Mapping):
            raise HistoryNormalizeError(f"history bar must be a mapping, got {row!r}")
        trade_date = trade_date_of_bar(row.get(_TIME_MS))
        provenance = provenance_for(trade_date)  # type: ignore[operator]
        bars.append(
            _row_to_bar(
                row,
                provider=provider,
                underlying=underlying,
                bar_type=bar_type,
                source=source,
                provenance=provenance,
            )
        )
    return tuple(bars)
