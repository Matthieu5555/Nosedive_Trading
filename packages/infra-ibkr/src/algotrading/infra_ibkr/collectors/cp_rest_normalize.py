"""Normalize IBKR Client Portal market data into our immutable ``RawMarketEvent``.

ADR 0024: the Client Portal REST/WebSocket path is a custom IBKR adapter (the Saxo/Deribit
pattern) feeding the same raw layer as the Nautilus-TWS path. Both the REST snapshot
(``/iserver/marketdata/snapshot``) and the WS market-data frame (``smd+CONID``) carry the same
**numeric field-tag codes**, so one normalizer serves both. It maps those tags onto the shared
field names in :mod:`.market_fields` and builds events through the shared
:func:`raw_market_event`, so the rows are identical to the Nautilus-TWS path for the same
observation — ADR 0024's equivalence bar (proven in ``test_cp_rest_equivalence.py``).

Pure and SDK-free → fully exercised in CI.
"""

import math
from collections.abc import Mapping
from datetime import datetime

from algotrading.infra.contracts import RawMarketEvent

from .market_fields import (
    ASK,
    ASK_SIZE,
    BID,
    BID_SIZE,
    LAST,
    LAST_SIZE,
    raw_market_event,
)

# Client Portal market-data field-tag codes → our canonical field names. Codes per the CP Web API
# (interactivebrokers.github.io/cpwebapi); they are constants here so a doc correction is one edit,
# and they MUST map onto the same names the Nautilus path uses or the equivalence test fails.
# Tuple (not dict) to fix a deterministic output order: bid, ask, sizes, last, last size.
_FIELDS: tuple[tuple[str, str], ...] = (
    ("84", BID),
    ("86", ASK),
    ("88", BID_SIZE),
    ("85", ASK_SIZE),
    ("31", LAST),
    ("7059", LAST_SIZE),
)

# The market-data field tags this normalizer understands (what to request on snapshot/subscribe).
REQUEST_FIELD_TAGS: tuple[str, ...] = tuple(tag for tag, _name in _FIELDS)

# IBKR's "no value available" sentinel (mirrors the TWS adapter's -1 drop).
_NO_VALUE = -1.0


def _parse_value(raw: object) -> float | None:
    """Parse a CP field value to a float, or ``None`` if absent / sentinel / non-finite.

    CP returns field values as strings, occasionally prefixed with a status flag (e.g. ``"C189.5"``
    when the last is the prior close, ``"H..."`` halted). Strip a leading non-numeric flag, then
    parse; drop the ``-1`` sentinel and any non-finite result.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if text and not (text[0].isdigit() or text[0] in "+-."):
        text = text[1:].strip()  # drop a leading status flag like 'C' / 'H'
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    if not math.isfinite(value) or value == _NO_VALUE:
        return None
    return value


def snapshot_to_events(
    row: Mapping[str, object],
    *,
    instrument_key: str,
    underlying: str,
    session_id: str,
    sequence: int,
    exchange_ts: datetime,
    receipt_ts: datetime,
) -> tuple[RawMarketEvent, ...]:
    """One CP market-data row (snapshot or WS frame) → its fields as ``RawMarketEvent`` rows.

    Only recognized, present, parseable fields become events; absent or sentinel values are
    skipped (never emitted as a fake observation). ``sequence`` is the per-session ordinal that
    makes a re-delivered row idempotent. ``exchange_ts`` is the row's update time
    (CP ``_updated`` ms); ``receipt_ts`` is when we received it.
    """
    events: list[RawMarketEvent] = []
    for tag, field_name in _FIELDS:
        if tag not in row:
            continue
        value = _parse_value(row[tag])
        if value is None:
            continue
        events.append(
            raw_market_event(
                instrument_key=instrument_key,
                underlying=underlying,
                session_id=session_id,
                field_name=field_name,
                value=value,
                sequence=sequence,
                exchange_ts=exchange_ts,
                receipt_ts=receipt_ts,
            )
        )
    return tuple(events)
