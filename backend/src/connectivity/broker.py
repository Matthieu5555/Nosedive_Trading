"""The broker-agnostic seam: the tick type and session protocol everything speaks.

This is the one boundary the rest of the system depends on. A concrete broker — a
live IBKR/Nautilus session, the in-memory fake, or the disk replay — hides
everything broker-shaped behind :class:`BrokerSession`. Crucially, the broker's
native tick-type enum is mapped to the plain string ``field_name`` *inside the
adapter*, so no broker enum ever crosses this line. That is what lets E's replay
emit the very same :class:`BrokerTick` the live adapter does and run the same
collector code over it (see ``tasks/02-market-data-plane.md``, Part IV.B).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

# ASCII unit separator: cannot occur in an instrument key or a field name, so joining
# on it builds a string whose parts are unambiguous before it is hashed into an id.
_ID_SEPARATOR = "\x1f"


@dataclass(frozen=True, slots=True)
class BrokerTick:
    """One normalized observation off the broker feed — broker-agnostic by design.

    Every field is a plain scalar. ``sequence`` is the feed's stable per-session
    ordinal for this observation; a tick re-delivered after a reconnect carries the
    *same* sequence, which is exactly what makes the collector's event id stable and
    its writes idempotent. ``exchange_ts`` is ``None`` when the feed provides no
    exchange time; the collector falls back to the receipt time for ordering.
    """

    broker_contract_id: str
    field_name: str
    value: float
    sequence: int
    exchange_ts: datetime | None = None


@runtime_checkable
class BrokerSession(Protocol):
    """The only place broker-specific behaviour is allowed to live.

    Every method is broker-agnostic in its types: ids and symbols are strings, chain
    rows are plain mappings, observations are :class:`BrokerTick`. A consumer depends
    on this Protocol, never on a broker SDK type.
    """

    def connect(self, client_id: int) -> None: ...

    def disconnect(self) -> None: ...

    def is_connected(self) -> bool: ...

    def request_option_chain(self, symbol: str) -> tuple[Mapping[str, object], ...]: ...

    def subscribe(self, broker_contract_id: str) -> None: ...

    def ticks(self) -> Iterator[BrokerTick]: ...


def content_event_id(instrument_key: str, field_name: str, sequence: int) -> str:
    """Deterministic, cross-process-stable event id for one observation.

    Keyed on the observation's content — the instrument, the field, and the feed's
    stable per-session sequence — so a tick re-delivered after a reconnect hashes to
    the *same* id (and is therefore deduplicated by the append-only store), while two
    genuinely distinct observations get distinct ids. It is SHA-256 of a canonical
    string, never Python's salted ``hash()``, so the id is identical across processes
    and machines without depending on ``PYTHONHASHSEED`` (the determinism rule in
    ``tasks/TESTING.md``).
    """
    payload = _ID_SEPARATOR.join((instrument_key, field_name, str(sequence)))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
