from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from algotrading.core.config import LoadedConfig, load_yaml_config

from .clock import Clock
from .errors import ClientIdError, ConnectionFailed, UnknownServiceError

_MAX_BACKOFF_EXPONENT = 32


class SupervisedSession(Protocol):

    def connect(self, client_id: int) -> None: ...

    def disconnect(self) -> None: ...

    def is_connected(self) -> bool: ...

    def request_option_chain(self, symbol: str) -> tuple[Mapping[str, object], ...]: ...

    def subscribe(self, broker_contract_id: str) -> None: ...


@dataclass(frozen=True, slots=True)
class BackoffSchedule:

    base_seconds: float = 1.0
    factor: float = 2.0
    cap_seconds: float = 30.0

    def delay_for(self, attempt: int) -> float:
        if attempt < 0:
            raise ValueError(f"backoff attempt must be >= 0, got {attempt}")
        exponent = min(attempt, _MAX_BACKOFF_EXPONENT)
        return min(self.cap_seconds, self.base_seconds * self.factor**exponent)


@dataclass(frozen=True, slots=True)
class BrokerConfig:

    client_id_bands: Mapping[str, int]
    client_id_band_width: int
    backoff: BackoffSchedule

    def __post_init__(self) -> None:
        if not self.client_id_bands:
            raise ValueError("broker config must define at least one client-id band")
        if self.client_id_band_width <= 0:
            raise ValueError(
                f"client_id_band_width must be > 0, got {self.client_id_band_width}"
            )

    def client_id_for(self, service: str, instance: int = 0) -> int:
        try:
            band = self.client_id_bands[service]
        except KeyError:
            raise UnknownServiceError(service, tuple(sorted(self.client_id_bands))) from None
        if not 0 <= instance < self.client_id_band_width:
            raise ClientIdError(service, instance, self.client_id_band_width)
        return band + instance

    @classmethod
    def from_config(cls, config: LoadedConfig) -> BrokerConfig:
        data = config.data
        reconnect = data["reconnect"]
        return cls(
            client_id_bands={str(k): int(v) for k, v in data["client_id_bands"].items()},
            client_id_band_width=int(data["client_id_band_width"]),
            backoff=BackoffSchedule(
                base_seconds=float(reconnect["base_seconds"]),
                factor=float(reconnect["factor"]),
                cap_seconds=float(reconnect["cap_seconds"]),
            ),
        )


def load_broker_config(configs_dir: str | Path) -> BrokerConfig:
    return BrokerConfig.from_config(load_yaml_config(Path(configs_dir) / "broker.yaml"))


@dataclass(frozen=True, slots=True)
class GapInterval:

    started_at: datetime
    ended_at: datetime

    def duration_seconds(self) -> float:
        return (self.ended_at - self.started_at).total_seconds()


class SessionSupervisor:

    def __init__(
        self,
        session: SupervisedSession,
        *,
        client_id: int,
        clock: Clock,
        backoff: BackoffSchedule | None = None,
        max_reconnect_attempts: int | None = None,
    ) -> None:
        self._session = session
        self._client_id = client_id
        self._clock = clock
        self._backoff = backoff if backoff is not None else BackoffSchedule()
        self._max_reconnect_attempts = max_reconnect_attempts
        self._subscriptions: list[str] = []
        self.reconnects: list[GapInterval] = []

    @property
    def client_id(self) -> int:
        return self._client_id

    @property
    def reconnect_count(self) -> int:
        return len(self.reconnects)

    def connect(self) -> None:
        self._connect_with_backoff()

    def is_healthy(self) -> bool:
        return self._session.is_connected()

    def disconnect(self) -> None:
        self._session.disconnect()

    def subscribe(self, broker_contract_id: str) -> None:
        self._subscriptions.append(broker_contract_id)
        self._session.subscribe(broker_contract_id)

    def request_option_chain(self, symbol: str) -> tuple[Mapping[str, object], ...]:
        return self._session.request_option_chain(symbol)

    def recover(self, dropped_at: datetime) -> GapInterval:
        self._connect_with_backoff()
        self._resubscribe()
        gap = GapInterval(started_at=dropped_at, ended_at=self._clock.now())
        self.reconnects.append(gap)
        return gap

    def _connect_with_backoff(self) -> None:
        attempt = 0
        while True:
            try:
                self._session.connect(self._client_id)
                return
            except ConnectionFailed:
                if (
                    self._max_reconnect_attempts is not None
                    and attempt >= self._max_reconnect_attempts
                ):
                    raise
                self._clock.sleep(self._backoff.delay_for(attempt))
                attempt += 1

    def _resubscribe(self) -> None:
        for broker_contract_id in self._subscriptions:
            self._session.subscribe(broker_contract_id)
