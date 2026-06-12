"""IBKR Client Portal market-data adapter (ADR 0024) — REST snapshot + WS stream → RawMarketEvent.

The adapter binds a set of subscribed instruments (our instrument key ↔ IBKR conid) and turns
Client Portal market data into our immutable ``RawMarketEvent`` rows via :mod:`.cp_rest_normalize`.
Two ingestion modes share that one normalizer:

* :meth:`snapshot` — a REST pull through the shared snapshot engine (:mod:`.cp_rest_snapshot`),
  inheriting its URI-safe conid batching (the HTTP-414 fix) and its cold-snapshot warm-up, so a
  freshly-subscribed line returns marks instead of metadata. Fully exercised in CI against a fake
  transport; this is the verifiable REST market-data path.
* :meth:`subscribe` / :meth:`_handle_frame` — the live WebSocket stream (``smd+conid``). The frame
  parsing is unit-tested; the socket itself runs only on a machine with a live CP Gateway.

**Read-only invariant (ADR 0024 §4):** the adapter only ever touches ``/iserver/marketdata/*`` and
the WS market-data topics — never an order endpoint. The read-only test asserts this against the
fake transport.
"""

import json
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from algotrading.infra.contracts import RawMarketEvent

from ..connectivity.cp_rest_transport import SupportsRestGet
from .cp_rest_normalize import REQUEST_FIELD_TAGS, snapshot_to_events
from .cp_rest_snapshot import snapshot_with_warmup
from .cp_rest_wire import SnapshotRow
from .market_fields import to_datetime

# The market-data field tags the adapter subscribes/snapshots — the ones the normalizer maps.
_REQUEST_FIELDS: tuple[str, ...] = REQUEST_FIELD_TAGS


@dataclass(frozen=True)
class CpInstrument:
    """One subscribed instrument: our canonical key, its IBKR conid, and its underlying."""

    instrument_key: str
    conid: int
    underlying: str


def _now_utc() -> datetime:
    return datetime.now(UTC)


def subscribe_message(conid: int) -> str:
    """The CP WebSocket market-data subscribe frame for one conid."""
    return f"smd+{conid}+{json.dumps({'fields': list(_REQUEST_FIELDS)})}"


def unsubscribe_message(conid: int) -> str:
    """The CP WebSocket market-data unsubscribe frame for one conid."""
    return f"umd+{conid}+{{}}"


class CpRestMarketDataAdapter:
    """Bind subscribed instruments and normalize CP market data into ``RawMarketEvent`` rows."""

    def __init__(
        self,
        transport: SupportsRestGet,
        instruments: Sequence[CpInstrument],
        *,
        session_id: str,
        now_fn: Callable[[], datetime] = _now_utc,
        _sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._transport = transport
        self._by_conid = {instrument.conid: instrument for instrument in instruments}
        self._session_id = session_id
        self._now_fn = now_fn
        self._sleep = _sleep
        self._sequence = 0
        self._tick_cb: Callable[[RawMarketEvent], None] | None = None
        self._fault_cb: Callable[[str], None] | None = None
        self._stop_event = threading.Event()

    def set_tick_callback(self, callback: Callable[[RawMarketEvent], None]) -> None:
        self._tick_cb = callback

    def set_fault_callback(self, callback: Callable[[str], None]) -> None:
        self._fault_cb = callback

    def snapshot(self) -> tuple[RawMarketEvent, ...]:
        """REST pull of the current quote for every subscribed instrument → events.

        Rides the shared snapshot engine: the request is split into URI-safe conid batches and
        each batch is warm-up polled, so a cold first snapshot does not come back metadata-only
        (the 414/cold-snapshot fixes the close capture proved live). ``_sleep`` is the injected
        warm-up sleep — tests poll with no real waiting.
        """
        rows = snapshot_with_warmup(
            self._transport, conids=tuple(self._by_conid), sleep=self._sleep
        )
        events: list[RawMarketEvent] = []
        for row in rows:
            events.extend(self._row_to_events(row, receipt_ts=self._now_fn()))
        return tuple(events)

    def _row_to_events(
        self, row: SnapshotRow, *, receipt_ts: datetime
    ) -> tuple[RawMarketEvent, ...]:
        instrument = self._by_conid.get(row.conid) if row.conid is not None else None
        if instrument is None:
            return ()
        exchange_ts = (
            to_datetime(row.updated_ms * 1_000_000) if row.updated_ms is not None else receipt_ts
        )
        events = snapshot_to_events(
            row,
            instrument_key=instrument.instrument_key,
            underlying=instrument.underlying,
            session_id=self._session_id,
            sequence=self._sequence,
            exchange_ts=exchange_ts,
            receipt_ts=receipt_ts,
        )
        self._sequence += 1
        return events

    def _handle_frame(self, raw: str | bytes) -> None:
        """Parse one CP WebSocket market-data frame and emit its events to the tick callback."""
        try:
            message = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(message, Mapping):
            return
        topic = message.get("topic")
        if not (isinstance(topic, str) and topic.startswith("smd+")):
            return  # control frames (heartbeats, system) are not observations
        row = SnapshotRow.model_validate(message)
        for event in self._row_to_events(row, receipt_ts=self._now_fn()):
            if self._tick_cb is not None:
                self._tick_cb(event)

    def subscribe_frames(self) -> tuple[str, ...]:
        """The WS subscribe frames to send for the bound instruments (live wiring sends these)."""
        return tuple(subscribe_message(conid) for conid in self._by_conid)

    def unsubscribe_all(self) -> tuple[str, ...]:
        """Stop streaming: the WS unsubscribe frames for the bound instruments."""
        self._stop_event.set()
        return tuple(unsubscribe_message(conid) for conid in self._by_conid)
