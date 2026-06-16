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
    default_exercise_style,
    persist_outputs,
    run_analytics,
)
from .outputs import ActorOutputs

ANALYTICS_CLIENT = ClientId("ANALYTICS")

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _to_unix_nanos(moment: datetime) -> int:
    microseconds = (moment - _EPOCH) // timedelta(microseconds=1)
    return microseconds * 1000


def _from_unix_nanos(nanos: int) -> datetime:
    return _EPOCH + timedelta(microseconds=nanos // 1000)


@customdataclass
class RawMarketEventData(Data):

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
    moneyness_buckets: tuple[float, ...] | None = None
    session_open: bool = True


class AnalyticsActor(Actor):

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
