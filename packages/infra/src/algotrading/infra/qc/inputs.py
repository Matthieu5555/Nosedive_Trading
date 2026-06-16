from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class CollectorContinuityInput(Protocol):

    @property
    def session_id(self) -> str: ...

    @property
    def gap_count(self) -> int: ...

    @property
    def subscribed_count(self) -> int: ...

    @property
    def covered_count(self) -> int: ...


@runtime_checkable
class GridPointInput(Protocol):

    @property
    def underlying(self) -> str: ...

    @property
    def tenor_label(self) -> str: ...

    @property
    def target_delta(self) -> float: ...

    @property
    def delta(self) -> float: ...


@runtime_checkable
class IvSpreadInput(Protocol):

    @property
    def underlying(self) -> str: ...

    @property
    def tenor_label(self) -> str: ...

    @property
    def delta_band(self) -> str: ...

    @property
    def iv_spread(self) -> float: ...
