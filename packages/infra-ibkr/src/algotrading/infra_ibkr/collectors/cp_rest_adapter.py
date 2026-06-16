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

_REQUEST_FIELDS: tuple[str, ...] = REQUEST_FIELD_TAGS


@dataclass(frozen=True)
class CpInstrument:

    instrument_key: str
    conid: int
    underlying: str


def _now_utc() -> datetime:
    return datetime.now(UTC)


def subscribe_message(conid: int) -> str:
    return f"smd+{conid}+{json.dumps({'fields': list(_REQUEST_FIELDS)})}"


def unsubscribe_message(conid: int) -> str:
    return f"umd+{conid}+{{}}"


class CpRestMarketDataAdapter:

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
        try:
            message = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(message, Mapping):
            return
        topic = message.get("topic")
        if not (isinstance(topic, str) and topic.startswith("smd+")):
            return
        row = SnapshotRow.model_validate(message)
        for event in self._row_to_events(row, receipt_ts=self._now_fn()):
            if self._tick_cb is not None:
                self._tick_cb(event)

    def subscribe_frames(self) -> tuple[str, ...]:
        return tuple(subscribe_message(conid) for conid in self._by_conid)

    def unsubscribe_all(self) -> tuple[str, ...]:
        self._stop_event.set()
        return tuple(unsubscribe_message(conid) for conid in self._by_conid)
