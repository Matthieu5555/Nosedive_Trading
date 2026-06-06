"""Host the pure analytics inside Nautilus's engine — Nautilus is the runtime spine.

ADR 0023 makes ``nautilus_trader`` the runtime. This module hosts our
framework-independent :func:`run_analytics` (see :mod:`.driver`) inside a Nautilus
``Actor`` and replays a :class:`RawMarketEvent` stream through Nautilus's backtest
engine on its *simulated* clock. The volatility math is unchanged — only *who drives
the events* changes. Live and replay run through the one engine, which is how the
single-code-path mandate (no historical-only fork) is realized by Nautilus rather than
by a hand-rolled loop.

Our immutable :class:`RawMarketEvent` + :class:`ParquetStore` stays the system of record
(ADR 0019); Nautilus events are bridged to/from ``RawMarketEvent`` here and never the
other way round. The determinism gate (``tests/test_nautilus_replay_byte_identical.py``)
proves this host returns the same :class:`ActorOutputs` — stamps included — as calling
:func:`run_analytics` directly, so ``as_of``/``calc_ts`` injection and the no-clock,
no-RNG discipline carry through the engine unchanged.
"""
# NOTE: no ``from __future__ import annotations`` here on purpose — Nautilus's
# ``@customdataclass`` builds an Arrow schema by introspecting real annotation *types*
# on ``RawMarketEventData``, which stringized annotations would break. Python 3.13
# evaluates the PEP 604 unions below at runtime fine without it.

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from algotrading.core.config import PlatformConfig
from algotrading.infra.contracts import (
    InstrumentKey,
    InstrumentMaster,
    Position,
    RawMarketEvent,
)
from algotrading.infra.storage import ParquetStore
from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.common.actor import Actor
from nautilus_trader.common.config import ActorConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.core.data import Data
from nautilus_trader.model.custom import customdataclass
from nautilus_trader.model.data import CustomData, DataType
from nautilus_trader.model.identifiers import ClientId

from .driver import (
    DEFAULT_MONEYNESS_BUCKETS,
    default_exercise_style,
    persist_outputs,
    run_analytics,
)
from .outputs import ActorOutputs

# The data client every raw observation is routed under. There is no broker venue in
# this path — the events are pre-captured RawMarketEvents — so a plain client id is the
# seam Nautilus's DataEngine routes custom data on.
ANALYTICS_CLIENT = ClientId("ANALYTICS")

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _to_unix_nanos(moment: datetime) -> int:
    """Exact UTC-datetime → unix nanoseconds (lossless for microsecond inputs).

    Integer microsecond arithmetic, then ``*1000``: a ``RawMarketEvent`` timestamp is
    microsecond-precision, so this round-trips byte-for-byte with :func:`_from_unix_nanos`.
    """
    microseconds = (moment - _EPOCH) // timedelta(microseconds=1)
    return microseconds * 1000


def _from_unix_nanos(nanos: int) -> datetime:
    """Unix nanoseconds → UTC datetime, the exact inverse of :func:`_to_unix_nanos`."""
    return _EPOCH + timedelta(microseconds=nanos // 1000)


@customdataclass
class RawMarketEventData(Data):
    """A :class:`RawMarketEvent` carried as a Nautilus custom data point.

    Scalar carriers only (Nautilus serializes custom data via an Arrow schema built from
    these annotations). ``ts_event``/``ts_init`` (supplied by the ``customdataclass``
    machinery) both carry the event's ``canonical_ts`` in nanoseconds — the time Nautilus
    orders and replays on — so the engine's simulated clock advances on our canonical time.
    """

    session_id: str = ""
    event_id: str = ""
    instrument_key: str = ""
    field_name: str = ""
    value: float = 0.0
    underlying: str = ""
    trade_date: str = ""
    exchange_ts_ns: int = 0
    receipt_ts_ns: int = 0


def to_custom_data(event: RawMarketEvent) -> CustomData:
    """Bridge one immutable ``RawMarketEvent`` into a Nautilus custom data point."""
    point = RawMarketEventData(
        ts_event=_to_unix_nanos(event.canonical_ts),
        ts_init=_to_unix_nanos(event.canonical_ts),
        session_id=event.session_id,
        event_id=event.event_id,
        instrument_key=event.instrument_key,
        field_name=event.field_name,
        value=event.value,
        underlying=event.underlying,
        trade_date=event.trade_date.isoformat(),
        exchange_ts_ns=_to_unix_nanos(event.exchange_ts),
        receipt_ts_ns=_to_unix_nanos(event.receipt_ts),
    )
    return CustomData(DataType(RawMarketEventData), point)


def from_custom_data(point: RawMarketEventData) -> RawMarketEvent:
    """Inverse of :func:`to_custom_data` — reconstruct the immutable ``RawMarketEvent``.

    Lossless by construction: the string/float fields are carried verbatim and every
    timestamp round-trips exactly (microsecond precision), so an event bridged out and
    back in is equal to the original. The determinism test asserts this directly.
    """
    return RawMarketEvent(
        session_id=point.session_id,
        event_id=point.event_id,
        instrument_key=point.instrument_key,
        exchange_ts=_from_unix_nanos(point.exchange_ts_ns),
        receipt_ts=_from_unix_nanos(point.receipt_ts_ns),
        canonical_ts=_from_unix_nanos(point.ts_event),
        field_name=point.field_name,
        value=point.value,
        trade_date=date.fromisoformat(point.trade_date),
        underlying=point.underlying,
    )


@dataclass(frozen=True)
class RunRequest:
    """Everything the analytics step needs that is *not* the event stream.

    These are injected (never read from a clock or the environment) so the run is a pure
    function of the events plus this request — the property the determinism gate relies on.
    """

    positions: Sequence[Position]
    instruments: Sequence[InstrumentKey]
    masters: Sequence[InstrumentMaster]
    config: PlatformConfig
    config_hashes: Mapping[str, str]
    as_of: datetime
    calc_ts: datetime
    store: ParquetStore | None = None
    persist: bool = False
    exercise_style_for: Callable[[InstrumentKey], str] = default_exercise_style
    moneyness_buckets: tuple[float, ...] = DEFAULT_MONEYNESS_BUCKETS
    session_open: bool = True


class AnalyticsActor(Actor):
    """A thin Nautilus ``Actor`` that drives :func:`run_analytics` and stamps its outputs.

    Holds no math: it accumulates the replayed :class:`RawMarketEvent` stream and, when the
    engine stops, computes the derived outputs over the *injected* ``as_of``/``calc_ts``
    and (optionally) persists them. The resulting :class:`ActorOutputs` is read off
    :attr:`outputs` after the engine run.
    """

    def __init__(self, config: ActorConfig, *, request: RunRequest) -> None:
        super().__init__(config)
        self._request = request
        self._events: list[RawMarketEvent] = []
        self.outputs: ActorOutputs = ActorOutputs()

    def on_start(self) -> None:
        self.subscribe_data(DataType(RawMarketEventData), client_id=ANALYTICS_CLIENT)

    def on_data(self, data: Data) -> None:
        point = data.data if isinstance(data, CustomData) else data
        self._events.append(from_custom_data(point))

    def on_stop(self) -> None:
        request = self._request
        self.outputs = run_analytics(
            self._events,
            request.positions,
            instruments=request.instruments,
            masters=request.masters,
            config=request.config,
            config_hashes=request.config_hashes,
            as_of=request.as_of,
            calc_ts=request.calc_ts,
            exercise_style_for=request.exercise_style_for,
            moneyness_buckets=request.moneyness_buckets,
            session_open=request.session_open,
        )
        if request.store is not None and request.persist:
            persist_outputs(request.store, self.outputs)


def run_session_via_nautilus(
    events: Sequence[RawMarketEvent],
    request: RunRequest,
) -> ActorOutputs:
    """Replay ``events`` through Nautilus's engine into the actor and return the outputs.

    This is the production seam and the determinism harness: Nautilus orders the events on
    its simulated clock by ``canonical_ts`` and feeds them to :class:`AnalyticsActor`, which
    runs the unchanged pure analytics. The returned :class:`ActorOutputs` is byte-identical
    to calling :func:`run_analytics` on the same events directly.
    """
    engine = BacktestEngine(
        config=BacktestEngineConfig(
            trader_id="ANALYTICS-001",
            logging=LoggingConfig(bypass_logging=True),
        )
    )
    actor = AnalyticsActor(ActorConfig(), request=request)
    engine.add_actor(actor)
    if events:
        engine.add_data([to_custom_data(event) for event in events], client_id=ANALYTICS_CLIENT)
    engine.run()
    outputs = actor.outputs
    engine.dispose()
    return outputs
